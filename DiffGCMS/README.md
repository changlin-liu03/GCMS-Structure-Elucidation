# DiffGCMS

> **Monorepo directory note:** In this document, “repository root” means the `DiffGCMS/` subdirectory. After cloning the parent repository, run `cd DiffGCMS` before following these commands. Local environments, configuration and `.env` (if used) belong in this subdirectory. See the [project overview](../README.md).

English | [简体中文](README.zh-CN.md)

**GC-EI-MS-conditioned discrete molecular graph diffusion with a SpecTUS encoder.**

DiffGCMS generates molecular bonds conditioned on a GC-EI-MS spectrum and a given set of heavy-atom types and counts. The default workflow first freezes the SpecTUS encoder and trains the condition projector and graph Transformer, then optionally fine-tunes the last four encoder layers.

The project integrates the pretrained spectrum encoder from [SpecTUS](https://github.com/hejjack/SpecTUS) with the discrete graph diffusion decoder from [DiffMS](https://github.com/coleygroup/DiffMS) for GC–EI–MS molecular structure inference. This repository provides the integrated implementation, configurations, and training and evaluation workflows. The original encoder and decoder methods and implementations are credited to their respective upstream projects.

> **Required inputs:** The model conditions on both a spectrum and a supplied heavy-atom composition. Training and testing obtain atom types and node counts from the `node_types` field in each data record. Diffusion operates on edges only. Datasets and model weights must be prepared separately.

## Model overview

```text
Spectrum: m/z tokens + intensity log-bins
                   │
       SpecTUS encoder (12 layers, 1024 dimensions)
                   │
       Mask-aware mean pooling over peaks
                   │
       Condition projector (1024 → 4096 → 1024)
                   │
                   └───────────────────┐
                                       ▼
Given atom types and counts → Graph Transformer → Discrete edge denoising
                                                           │
                                                   Candidate molecules
```

The default decoder has 4 layers and uses 500 diffusion steps. It supports 10 heavy-atom types and 5 edge classes: no bond, single, double, triple, and aromatic. See [Model architecture](model.md) for details.

## Installation

Run all commands from the repository root. The declared Python requirement is ≥ 3.9. To reproduce a particular experiment, use the dependency environment associated with that training run.

1. Create and activate a dedicated Python environment.
2. Install PyTorch appropriate for your CPU or CUDA environment.
3. Install the project and its declared dependencies:

```bash
python -m pip install -e .
```

The main dependencies are PyTorch, PyTorch Geometric, PyTorch Lightning, Transformers, Hydra, and RDKit. Install a hardware-compatible PyTorch build before installing the project dependencies.

The `.sh` scripts below require Bash. On Windows, use WSL, or use the `python -m src.gcms_main` commands after preparing the environment, weights, and statistics.

Defaults use one GPU, `bf16-mixed` precision, and a training batch size of 128. Adjust these settings to your hardware. For CPU debugging, you can override `general.gpus=0 train.precision=32-true train.num_workers=0 train.batch_size=2 train.eval_batch_size=2`; running the full model on a CPU remains computationally expensive.

## Data preparation

By default, place `DiffGCMS/` and `dataset/` under the same parent directory:

```text
parent/
├── DiffGCMS/
└── dataset/
    └── split_90_5_5/
        ├── train.jsonl
        ├── valid.jsonl
        ├── test.jsonl
        └── stats/
            ├── atom_types.txt
            ├── edge_types.txt
            ├── n_counts.txt
            └── valencies.txt
```

`split_90_5_5` is the default directory convention; the program does not split the data automatically. See [Data format](docs/data.md) for fields and preprocessing requirements. Generate statistics from the training set:

```bash
python -m scripts.compute_stats --jsonl ../dataset/split_90_5_5/train.jsonl --stats-dir ../dataset/split_90_5_5/stats
```

Both training and testing require all three data files and all four statistics files. Initialization reads a training batch to determine model input dimensions.

## Preparing the SpecTUS encoder

The extraction script retrieves `pytorch_model.bin` from the configured Hugging Face model, `MS-ML/SpecTUS_pretrained_only`, and extracts encoder tensors:

```bash
python scripts/extract_spectus_encoder.py
```

Alternatively, provide a local PyTorch checkpoint:

```bash
python scripts/extract_spectus_encoder.py --source /path/to/pytorch_model.bin
```

The output directory is `checkpoints/spectus_encoder/`. It contains `.pt` tensors, corresponding `.key` files, and `manifest.json`. Allow sufficient storage and memory for the weights.

The extractor uses PyTorch-format weights. The directory specified by `--output` is deleted and recreated: use a dedicated weight directory, and load weights only from trusted sources.

## Training, fine-tuning, and testing

### Frozen-encoder training

```bash
bash scripts/train.sh
```

The script attempts to extract encoder weights if they are missing and generates statistics if `atom_types.txt` is missing. Ensure that all four statistics files are present before running. Once preparation is complete, you can also run:

```bash
python -m src.gcms_main --config-name=config_train
```

The default run trains for 30 epochs with a frozen encoder and a trainable projector and graph decoder. Validation computes NLL and edge-classification metrics; molecular sampling during validation is disabled by default. Testing is not run automatically after training.

### Fine-tuning the last four layers

Select a checkpoint from the frozen-encoder stage:

```bash
bash scripts/train_last4.sh /absolute/path/to/selected.ckpt
```

Or run:

```bash
python -m src.gcms_main --config-name=config_train_last4 "general.load_weights='/absolute/path/to/selected.ckpt'"
```

This stage trains encoder `layers[8:12]`, the projector, and the graph decoder for 15 epochs by default. `general.load_weights` loads model weights without restoring the optimizer or epoch from the previous stage.

### Resuming the same stage

```bash
python -m src.gcms_main --config-name=config_train "general.resume='/absolute/path/to/last.ckpt'"
```

Use `--config-name=config_train_last4` to resume fine-tuning. Match the original stage and configuration when using `resume`; use `load_weights` when moving between stages.

### Testing and candidate generation

```bash
bash scripts/test.sh /absolute/path/to/selected.ckpt
```

Or run:

```bash
python -m src.gcms_main --config-name=config_test "general.test_only='/absolute/path/to/selected.ckpt'"
```

By default, the model generates 10 candidates per input, with 500 denoising steps per candidate. This guide uses single-GPU testing. Prepare the separate SpecTUS encoder directory, all three data splits, and the statistics files.

Append Hydra overrides to a script command as needed:

```bash
bash scripts/test.sh /absolute/path/to/selected.ckpt train.eval_batch_size=16 general.test_samples_to_generate=5
```

For checkpoint paths containing spaces or `=`, use the quoting pattern shown in the Python commands above.

## Configuration and outputs

| Configuration group | Purpose |
|---|---|
| `configs/config_*.yaml` | Compose configurations and set the Hydra output directory |
| `configs/general/` | Run names, weights, encoder strategy, sampling, and logging |
| `configs/train/` | Learning rates, batch sizes, precision, and epochs |
| `configs/model/medium.yaml` | Encoder, decoder, and diffusion architecture |
| `configs/dataset/gcms_train.yaml` | Data paths, atom vocabulary, and sample limits |

Hydra changes the working directory to `outputs/<date>/<time>-<run_name>/`. Run artifacts are written there:

```text
outputs/<date>/<time>-<run_name>/
├── .hydra/                    # Configuration for this run
├── gcms_main.log
├── logs/<run_name>/           # CSV and optional W&B logs
├── checkpoints/<run_name>/    # Training checkpoints
└── preds/                     # Test JSON and per-batch pickle files
```

Training checkpoints are selected using `val/NLL` by default. They are separate from `checkpoints/spectus_encoder/` at the repository root. To resume training, use the checkpoint path inside the relevant run output directory.

Relative weight paths are resolved against the repository root; relative data paths are resolved against its parent directory. For custom data locations, override both `dataset.datadir` and `dataset.stats_dir`. The Bash training scripts still generate statistics using the default data location. For custom paths, generate statistics manually and use the Python entry point.

Default run names are `DiffGCMS_medium`, `DiffGCMS_last4`, and `DiffGCMS_test`. The package and W&B project are named `DiffGCMS`. Existing run directories are not renamed automatically.

## Repository structure

```text
DiffGCMS/
├── configs/                 # Hydra configurations
├── scripts/                 # Launchers, statistics, and weight extraction
├── src/
│   ├── gcms_main.py          # Entry point
│   ├── diffusion_model_gcms.py
│   ├── datasets/            # JSONL loading and molecular graph construction
│   ├── models/              # Spectrum encoder and graph Transformer
│   ├── diffusion/           # Noise, posteriors, sampling, and extra features
│   ├── metrics/             # Losses and evaluation metrics
│   └── analysis/            # Molecular reconstruction, drawing, and RDKit tools
├── docs/                    # Data and experiment documentation
├── checkpoints/             # Local encoder weights
├── model.md                 # Model architecture
└── pyproject.toml           # Package metadata and dependencies
```

Each guide includes a link to its Chinese version.

## Reproducing experiments

Keep the code version, dependency environment, data split, run configuration, and checkpoint associated with each experiment. Use consistent candidate generation and evaluation settings. See [Experiment reproducibility](docs/reproducibility.md) for run records, prediction files, and metric conventions.

## Sources and licenses

| Component | Source | License information |
|---|---|---|
| Spectrum encoder | [SpecTUS](https://github.com/hejjack/SpecTUS) | Upstream source code is MIT-licensed; its copyright and license are retained |
| Graph diffusion decoder and related components | [DiffMS](https://github.com/coleygroup/DiffMS) | Upstream source code is MIT-licensed and is itself adapted from DiGress |
| Inherited graph diffusion foundations | [DiGress](https://github.com/cvignac/DiGress) | Its MIT copyright and license are retained |
| Default SpecTUS pretrained weights | [MS-ML/SpecTUS_pretrained_only](https://huggingface.co/MS-ML/SpecTUS_pretrained_only) | The model card specifies CC BY-NC 4.0, separately from the code license |

See [LICENSE](LICENSE) for the MIT terms and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for component provenance and retained notices. Original copyright holders remain credited; the DiffGCMS name does not replace upstream ownership.

External datasets and pretrained weights are not distributed with this code repository. The code's MIT license does not relicense pretrained weights as MIT. Extracted encoders and checkpoints containing these parameters do not become MIT-licensed simply because this project produced them. Follow the terms of the actual weight source when using or distributing them. The default weights' [CC BY-NC 4.0 license](https://creativecommons.org/licenses/by-nc/4.0/) includes attribution and noncommercial conditions.

## Citation

When using this repository in research, please also cite the upstream methods you use:

- Adam Hájek, Helge Hecht, Elliott J. Price, Aleš Křenek. *SpecTUS: Spectral Translator for Unknown Structures annotation from EI-MS spectra*. arXiv:2502.05114v1, 2025. [Paper](https://arxiv.org/abs/2502.05114v1)
- Montgomery Bohde, Mrunali Manjrekar, Runzhong Wang, Shuiwang Ji, Connor W. Coley. *DiffMS: Diffusion Generation of Molecules Conditioned on Mass Spectra*. ICML, PMLR 267:4737–4756, 2025. [Paper](https://proceedings.mlr.press/v267/bohde25a.html)

BibTeX entries are provided in [references.bib](references.bib). The SpecTUS entry identifies the 2025 v1 preprint referenced by this project. If you cite another version, use that version's authors and publication information. Academic citation and software license retention are separate requirements.
