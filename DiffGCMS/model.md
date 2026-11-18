# DiffGCMS Model Architecture

English | [简体中文](model.zh-CN.md)

This guide describes the current source code and default `medium` configuration. See the [README](README.md) for commands and [Data format](docs/data.md) for input fields. The sections below follow the data flow through the encoder, graph decoder, diffusion process, and evaluation.

## 1. Task and conditioning

DiffGCMS generates molecular graphs from spectra and a supplied heavy-atom set. With the default `denoise_nodes=false`, node types and counts remain fixed. Only edge categories undergo diffusion during training and candidate generation.

Node conditions come from `node_types` in reference molecular records. The model does not predict molecular formulas or infer node counts independently from spectra. Explicit hydrogens, formal charges, and stereochemistry are not separate output categories of the graph decoder. RDKit handles implicit hydrogens based on the generated structure.

## 2. Modules and data flow

```text
JSONL record
├── input_ids / position_ids → padding + attention_mask
│                                      │
│                              GCMSpectrumEncoder
│                                      │
│                              Mask-aware mean pooling h0
│                                      │
│                              GCMSConditionProjector → y
└── node_types / edge_index / edge_types → PyG Batch
                                              │
                                    to_dense → X, E, node_mask
                                              │
                                    Noise E; keep X fixed
                                              │
                              Structural + chemical features + time
                                              │
                                      GraphTransformer
                                              │
                                Edge logits → loss / reverse sampling
```

`src/gcms_main.py` assembles the components. `GCMSDenoisingDiffusion` in `src/diffusion_model_gcms.py` handles the Lightning lifecycle, optimizer, evaluation, and candidate generation.

## 3. Spectrum encoder

The encoder is defined in `src/models/gcms_encoder.py`.

| Setting | Default |
|---|---|
| m/z token range | 0–500 |
| Intensity log-bin range | 0–29 |
| Encoder layers / hidden dimension | 12 / 1024 |
| Attention heads / FFN dimension | 16 / 4096 |
| Dropout | 0.2 |

`input_ids` pass through an m/z embedding, and `position_ids` pass through an intensity embedding with offset=2. Their sum passes through LayerNorm, dropout, and BART encoder layers. `position_ids` represent intensity bins, not peak positions within the sequence.

Pooling includes only valid peaks. The projector is trained as part of this model:

```text
h0 = sum(hidden_states × attention_mask) / max(sum(attention_mask), 1)
y  = Linear(4096 → 1024)(SiLU(Linear(1024 → 4096)(h0)))
```

By default, the entire encoder is frozen and kept in eval mode to disable its dropout. Last-four-layer fine-tuning trains `layers[8:12]` while freezing the embeddings and the first eight layers. The code also provides `full`, `lastN`, and `last_n` strategies; the default workflows cover freezing and last-four-layer fine-tuning.

## 4. Molecular graphs and extra features

Default atom vocabulary: `C, N, O, F, Cl, S, P, Si, Br, I`.

Edge classes: `0=no bond, 1=single, 2=double, 3=triple, 4=aromatic`.

| Tensor | Shape | Meaning |
|---|---|---|
| `X` | `[B, N, 10]` | Atom one-hot features |
| `E` | `[B, N, N, 5]` | Edge one-hot features |
| `y` | `[B, 1024]` | Spectrum condition |
| `node_mask` | `[B, N]` | Valid nodes |

`N` is the largest node count in the batch. Edges remain symmetric. The diagonal of the dense edge tensor is zeroed and excluded from edge-classification loss.

For the default `extra_features=all`, the input dimensions follow from the code:

| Channel | Components | Dimension |
|---|---|---|
| `X` | Atom 10 + node cycle counts 3 + non-largest-component indicator 1 + eigenvectors 2 + chemical features 2 | 18 |
| `E` | Edge categories 5 | 5 |
| `y` | Spectrum 1024 + node count 1 + global cycle counts 4 + component count 1 + eigenvalues 5 + weight feature 1 + time 1 | 1037 |

The initial placeholder `y` in graph data is replaced by the spectrum condition and contributes no additional dimension. The `y_dim + 1` used during dimension inference accounts for time conditioning.

Structural features in `src/diffusion/extra_features.py` include node-level 3–5 cycles, graph-level 3–6 cycles, and Laplacian eigenvalues and eigenvectors. Cycle features are divided by 10 and capped at 1. These computations run in fp32 with mixed precision disabled.

Chemical auxiliary features are supplied by `ChargeFeature`, `ValencyFeature`, and `WeightFeature` in `src/diffusion/extra_features_molecular.py`. The first two each add one node feature; the last adds one global feature. Refer to that module for the computations. The five edge-class indices remain consistent across graph construction, diffusion, and molecular reconstruction.

## 5. Graph Transformer

The default decoder is `GraphTransformer` in `src/models/transformer_model.py`, built from `XEyTransformerLayer` and `NodeEdgeBlock`.

| Setting | Default |
|---|---|
| Layers | 4 |
| Input MLP hidden dimensions | X:256, E:128, y:1024 |
| Transformer hidden dimensions | X:256, E:64, y:512 |
| Attention heads | 8 |
| FFN dimensions | X:256, E:128, y:1024 |
| Output dimensions | X:10, E:5, y:1024 |

Each layer jointly updates node, edge, and global features. Nodes generate Q/K/V, edges modulate attention through multiplication and addition, and the spectrum condition modulates node and edge representations in a FiLM-style operation. Aggregated node and edge features update the global vector.

`Xtoy` and `Etoy` aggregate means, minima, maxima, and variances, then map them to the global feature space through linear layers.

The decoder adds residuals from the corresponding input channels. Edge outputs are masked on the diagonal and symmetrized. Although the network outputs X, E, and y, the default training loss uses only the edge term.

`GraphTransformerV2/V3/V4`, `NodeEdgeBlockV2`, and `FPMultiHeadAttention` in the same file are experimental implementations that are not wired into the default entry point. Their presence does not imply supported configuration options.

## 6. Discrete edge diffusion and loss

Defaults use a 500-step cosine schedule and empirical training-set marginals. For edge-class distribution `m_E`:

```text
Q_t = (1 − β_t) I + β_t · 1 m_Eᵀ
Q̄_t = ᾱ_t I + (1 − ᾱ_t) · 1 m_Eᵀ
```

Training samples `t ∈ {1, …, T}` and uses the clean edges and cumulative transition matrix to sample noisy edges. The upper triangle is retained and symmetrized. Node X and spectrum condition y remain fixed, and the decoder predicts clean-graph category logits.

The default `lambda_train=[0, 1, 0]` optimizes cross-entropy over valid edges. The node and global-condition loss weights are zero.

## 7. Validation and test metrics

The code computes an NLL-style variational objective containing a node-count distribution term, terminal KL, stepwise posterior KL, and a reconstruction term. Checkpoint selection uses `val/NLL`.

Candidate metrics are implemented in `src/metrics/diffms_metrics.py`:

- `validity`: the fraction of generated molecules that pass RDKit sanitization and contain exactly one connected component.
- `acc_at_k`: valid candidates are converted to InChI, ranked by occurrence frequency, and deduplicated. The metric checks whether the reference InChI appears in the first k entries.
- `tanimoto_at_k` / `cosine_at_k`: candidates are reconstructed from InChI and tautomer-canonicalized. Using radius-2, 2048-bit Morgan fingerprints, the highest similarity among the first k candidates is computed and then averaged across samples.

Frequency ties preserve the counter's insertion order. Frequency ranking is not calibrated confidence. Samples without a recoverable reference molecule are skipped for accuracy and similarity updates, while their generated molecules still contribute to validity.

## 8. Reverse sampling and outputs

Edges are initialized from the marginal distribution, while atom X and condition y remain fixed. Sampling proceeds from T to 0: predict clean-edge categories, combine them with the discrete posterior to obtain the preceding-step distribution, then sample and symmetrize.

This process is repeated for each input to generate multiple candidates, which are converted into RDKit molecules. Reconstruction does not use reference `atom_hydrogens`. Testing writes per-batch pickle files and candidate JSON. JSON preserves sampling order rather than the frequency ranking used for metrics.

See [Experiment reproducibility](docs/reproducibility.md) for data splits, outputs, and use of existing checkpoints.
