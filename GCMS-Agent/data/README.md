# Data guide

**English** | [简体中文](README.zh-CN.md)

The full research dataset is private and is not distributed with the GitHub repository. Apart from two explicitly authorized historical cases, this restriction also covers derived pilot subsets, individual cases, labels, selection manifests, and data copies. Public runnable examples are in [examples/](examples/README.md); they are independently constructed rather than extracted from research data.

## Two authorized real-case exceptions

The data holder explicitly authorized publication of spectra, candidate structures, reference answers, and edit histories for [Butyl butanoate and Trimethylpentylsilane](../case_studies/README.md). Field-selected materials are stored separately in `case_studies/`. This does not remove ignore rules for `data/raw_linear_v1/`, raw `runs/`, or other data, and does not authorize distribution of the full dataset.

Both display inputs use raw linear intensities. The main Agent run in each case is incomplete; the bundles retain that status and failures/retries. The synthetic `data/examples/` files serve a different purpose from these real historical cases.

## Directory roles

| Path | Purpose | Published with the repository? |
|---|---|---|
| `raw_linear_v1/inference/all.json` | Full label-free inputs | No, local only |
| `raw_linear_v1/inference/pilot.json` | 20 development samples stratified by candidate status | No, local only |
| `raw_linear_v1/inference/engineering_pilot.json` | 4 fixed development cases | No, local only |
| `raw_linear_v1/inference/single_top10_miss.json` | A fixed development case; filename retained from historical usage | No, local only |
| `raw_linear_v1/evaluation/` | Full and single-case evaluation labels | No, local only |
| `raw_linear_v1/manifests/` | Source/input hashes, selection indices, and local source paths | No, local only |
| [examples/](examples/README.md) | Synthetic inputs, demonstration reference labels, and documentation | Yes, only explicitly allowlisted files |

The root `.gitignore` excludes data under `data/` by default. It allows only `README.md`, `README.zh-CN.md`, `examples/README.md`, `examples/README.zh-CN.md`, `examples/inference.json`, and `examples/labels.json`. New data directories, backups beside examples, and other files are not automatically eligible for publication. Ignore rules do not control manual uploads or compression of the whole folder; publish using a reviewed file list. This guide does not grant permission to release private data.

## Inference input format

Input is a UTF-8 JSON array. Each item uses the following fields; actual validation is implemented in [loader.py](../src/gcms_agent/data/loader.py) and [intensity.py](../src/gcms_agent/data/intensity.py).

| Field | Contract |
|---|---|
| `sample_id` | 1–100 ASCII letters, digits, underscores, or hyphens; unique within the file |
| `input_ids` | Nonempty array of unique positive integer nominal m/z values |
| `intensity` | Same length as `input_ids`; finite nonnegative linear intensities with at least one positive value; decimals allowed; zeros are not observed peaks |
| `generated` | Exactly 10 nonempty candidate SMILES strings; duplicates allowed and source slots retained; downstream tools handle syntax and chemical validity |
| `conditions` | Optional object allowing only `ionization`, `energy_ev`, `energy_is_assumed`, `resolution`, and `intensity_encoding` |

Conditions currently support only `EI`, `nominal`, and `raw_linear`. If supplied, energy must be numeric, greater than 0 and less than 1000; the assumption flag must be Boolean. Legacy `position_ids` binning is unsupported. Intensities retain their original scale; scoring weights are `I/sum(I)`. A maximum of 999 is not required.

Inference inputs reject `true_smiles` and other unknown fields. Labels are a separate JSON array of items containing `sample_id` and `true_smiles`, used only for independent evaluation after a run. Example labels are manually assigned demonstration references.

## Local data preparation

Existing private `raw_linear_v1/` data can continue to be used locally without regeneration or manifest changes. Absolute source paths in manifests support local verification; they are not portable download addresses. The preparation tool defaults to `test.json` one directory above the project. That source file is not distributed; specify its actual path if it is elsewhere.

If authorized to use your own raw data, use the trusted preparation program to separate inputs and labels. Each source JSON item requires `input_ids`, `intensity`, and `generated`, with optional `conditions` and `true_smiles`. Source labels are separated during preparation and excluded from inference.

Run from the repository root in an environment with runtime dependencies installed. Replace the placeholder below with your own local path.

```powershell
$env:PYTHONPATH = (Resolve-Path ./src).Path
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'

./.venv/Scripts/python.exe -B -m gcms_agent.cli audit 'C:/local-data/source.json' --output data/local_prepared
```

Preparation generates `inference/all.json`, up to 20 samples in `inference/pilot.json`, `evaluation/labels.json`, and `manifests/data.json`. Existing outputs are not overwritten; use a new directory. [prepare_development_cases.py](../scripts/prepare_development_cases.py) can generate fixed development subsets separately. Its default indices belong to the original development dataset; specify valid indices explicitly for other data.

When evaluating a custom dataset, use `--labels` to select its labels. The CLI default is still local `raw_linear_v1/evaluation/labels.json`. See the [project homepage](../README.md) for installation and the [synthetic example guide](examples/README.md) for a workflow without private data.
