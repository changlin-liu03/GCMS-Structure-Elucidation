# DiffGCMS Experiment Reproducibility

English | [简体中文](reproducibility.zh-CN.md)

This guide covers experiment records, run configurations, prediction outputs, and existing checkpoints. See the [README](../README.md) for training and testing commands and [Data format](data.md) for input fields.

## Keeping experiment records

Retain the following information to associate code, weights, and results:

| Item | Suggested record |
|---|---|
| Code version | Git commit or the corresponding source snapshot |
| Software environment | Python, PyTorch, CUDA, PyG, Lightning, Transformers, RDKit, and other dependency versions |
| Hardware | GPU model and count, GPU memory, and operating system |
| Data and preprocessing | Dataset version, split criteria, sample counts, deduplication, and spectrum processing rules |
| Statistics | The four statistics files and their source training set |
| Run configuration | Hydra's saved configuration and command-line overrides |
| Weights | Encoder source, stage initialization weights, and final test checkpoint |
| Evaluation settings | Test set, candidate count, diffusion steps, and metric definitions |

Dependency versions can be exported from the actual training environment:

```bash
python --version
python -m pip freeze > environment-freeze.txt
```

For GPU runs, you can also retain the device and driver information reported by `nvidia-smi`. Hydra saves run configurations under `.hydra/` in the output directory; CSV logs are under `logs/`.

## Training stages and checkpoints

Frozen-encoder training and last-four-layer fine-tuning are separate stages:

- Use `config_train` to train the projector and graph decoder with the encoder frozen.
- Use `config_train_last4` and `general.load_weights` to initialize from a selected frozen-stage checkpoint, then train the last four encoder layers, projector, and graph decoder.
- Use `general.resume` to resume the same stage, keeping the original stage configuration.
- Use `config_test` and `general.test_only` to select the checkpoint for testing.

Training checkpoints are selected using `val/NLL` by default. Record the path and selection criterion for the final test checkpoint. Both training and testing require all three data splits, training-set statistics, and a separate SpecTUS encoder directory.

## Candidate outputs and metric conventions

This guide follows the default single-GPU testing workflow. Test JSON is written to `preds/<run_name>_predictions.json` within the run directory. Each record contains:

- `input_ids` and `position_ids`: original spectrum tokens.
- `true_smiles`: the reference SMILES from the data, or a string recovered from the reference molecule.
- `generated`: candidates in sampling order, each with `smiles`, `inchi`, and `valid`.

Each batch also produces pickle files containing RDKit molecules. Defaults generate 10 candidates per input with 500 denoising steps per candidate.

Candidate ranking for accuracy and similarity filters invalid molecules, ranks InChIs by occurrence frequency, and deduplicates them. The first k JSON entries may therefore differ from the candidates used for `acc_at_k`; frequency ranking is not a calibrated probability. See [Model architecture](../model.md#7-validation-and-test-metrics) for accuracy, similarity, and validity definitions.

When comparing experiments, keep the data sets, candidate counts, structure standardization, and metric computations consistent. Report full-test-set and subset results separately.

## Existing checkpoints and run names

The display name, package name, and W&B project name are `DiffGCMS`. Default run names are `DiffGCMS_medium`, `DiffGCMS_last4`, and `DiffGCMS_test`.

Naming updates do not change model tensor structure or move existing files. Supply existing checkpoint paths explicitly and use the model configuration associated with training. Retaining the original source, weights, and logs makes comparisons between experiment versions easier.
