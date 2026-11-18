# Script guide

**English** | [简体中文](README.md)

This directory provides data preparation, offline checks, live-model validation, and historical experiment utilities. Use the project CLI for ordinary batch inference, baselines, and independent evaluation; see the [project homepage](../README.md). Scripts are not executed automatically by the default workflow.

## Prerequisites

Install runtime dependencies first; tests also require pytest. Run these commands in PowerShell from the repository root, explicitly selecting this checkout to avoid importing a different copy from an existing environment.

```powershell
$env:PYTHONPATH = (Resolve-Path ./src).Path
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
```

Follow the [installation guide](../README.md#install-from-scratch) to create an isolated environment and install the project in editable mode. Examples use that `.venv` interpreter; another interpreter with the dependencies installed may be substituted. Data files, historical results, and local credentials must be supplied separately. Obtaining source code does not supply those inputs.

## Offline data and chemistry tools

These scripts do not call a model, but some write files.

| Script | Inputs and prerequisites | Outputs and limitations |
|---|---|---|
| [prepare_development_cases.py](prepare_development_cases.py) | Prepared data directory and original source file; verifies the source hash. Defaults: `data/raw_linear_v1/` and `test.json` above the project | Writes single-case inputs/labels, engineering subset, and selection manifest, overwriting the corresponding subset files. Default single-case index: 0; engineering indices: 85, 120, 294, 313, all indexed from 0. Reads labels for export; selection uses fixed indices |
| [economy_pilot.py](economy_pilot.py) `prepare` | Isolated label-free inference inputs; stratifies by candidate count, peak count, heavy-atom count, and validity | Writes the requested JSON and a matching `.manifest.json`; defaults to 20 samples and seed 20261103; does not start inference |
| [economy_pilot.py](economy_pilot.py) `report` | `logs/budget.json`, results, and manifests from one or more run directories; optional sampling manifest | Prints a cost report, optionally saved with `--output`. Extrapolation requires adequate completeness and usage records. Default population: 1490, adjustable with `--population`. Uses code-defined price snapshots, not live invoices |
| [validate_dataset.py](validate_dataset.py) | Label-free inference input | Checks candidate validity and graph round trips; defaults to `runs/dataset_validation.json`, adjustable with `--output` |
| [validate_molecode_skill.py](validate_molecode_skill.py) | Actual repository skill Markdown and `tests/fixtures/molecode_skill_cases.json` | Prints instructional-example validation without writing experimental results; checks document/tool consistency, not model reasoning accuracy |
| [validate_silicon_rules.py](validate_silicon_rules.py) | Requires `--input`, `--labels`, `--old-policy`, and `--output`; newly enabled rules must exactly match the expected silicon rules | Writes rule copies, selection manifest, per-case comparisons, and summary into a new directory. Includes the historical case870 development-set convention. Labels are for offline diagnostics; results are not an independent external benchmark |

Unless a script explicitly rejects an existing destination, output files may be overwritten. Select appropriate new output paths. Development subsets and historical development cases must not be treated as untouched holdout test sets.

```powershell
# Validate instructional examples only; the full experimental dataset is unnecessary.
./.venv/Scripts/python.exe -B scripts/validate_molecode_skill.py

# Show help for entry points with argument parsers, without preparing or validating data.
./.venv/Scripts/python.exe -B scripts/prepare_development_cases.py --help
./.venv/Scripts/python.exe -B scripts/economy_pilot.py prepare --help
./.venv/Scripts/python.exe -B scripts/economy_pilot.py report --help
./.venv/Scripts/python.exe -B scripts/validate_dataset.py --help
./.venv/Scripts/python.exe -B scripts/validate_silicon_rules.py --help
```

## Local mock-service checks

| Script | Checks | Requirements and output |
|---|---|---|
| [validate_thinking_transport.py](validate_thinking_transport.py) | Allowlisted fields actually sent by the SDK/CLI, including thinking, model, and output limits | Starts a mock Messages service on `127.0.0.1` with fake credentials; requires a working Claude Agent SDK/CLI. Prints results, optionally saved with `--output` |
| [validate_progress_watchdog.py](validate_progress_watchdog.py) | Interrupt scenarios such as output truncation, no progress, and session character limits | Also uses a local mock service and fake credentials; requires the SDK/CLI, subprocess execution, and local listening permissions. Prints results, optionally saved with `--output` |

Neither calls a live model service. Unlike default pytest checks based only on Python mocks, they start real CLI subprocesses. Results characterize transport and cancellation behavior of locally installed versions, not model quality or actual cost.

## Live-model entry points

These entry points make real model requests, require `DEEPSEEK_API_KEY`, and may incur charges. Check the [configuration guide](../configs/README.en.md) and local model, service, and budget settings first.

| Script | Purpose and input | Output and configuration |
|---|---|---|
| [probe.py](probe.py) | Uses a built-in ethanol case to check the live tool-call chain | Always writes `runs/probe/result.json`, overwriting on repetition. A single-response limit of 8192 tokens and at most 8 tool turns; calls Harness directly, bypassing batch-budget and time-window scheduling |
| [run_single_miss.py](run_single_miss.py) | Reads the fixed single case and selection manifest from the current data directory; verifies source and input hashes | Requires `--output`; uses economy. Supports `--thinking`, `--max-output-tokens`, and `--allow-peak`. Labels are loaded for evaluation only after freezing predictions. The historical filename is retained; current preparation does not reselect cases using ground truth |
| [validate_economy_profile.py](validate_economy_profile.py) | Reads `engineering_pilot.json` from the current data directory, normally containing four development cases | Requires `--output`; uses economy. `--budget-cny` sets a positive monetary limit. Writes results and `engineering_report.json`; does not establish full-dataset accuracy or cost targets |

The latter two support `--data-root` and `--ask-key` and reject destinations already containing `manifest.json`; use a new output directory. If no monetary limit is explicitly supplied, configuration applies. A script's name does not imply a fixed monetary cap.

`probe.py` has no argument parser: **do not run `probe.py --help` to inspect help**, because it still makes a live call. Read this guide or its source instead. `validate_molecode_skill.py` also has no argument parser; running it directly performs offline validation.

## Public case-study export

[export_case_studies.py](export_case_studies.py) handles only the three fixed cases authorized for publication, producing [case_studies/](../case_studies/README.en.md) from local frozen records. Original `runs/` must be available. The script verifies frozen hashes, selects fields, deduplicates scientific objects, and retains every recorded trial and incomplete status. It makes no model calls, does not rescore results, and does not read the full private dataset or session workspaces.

The project Python environment, RDKit, and Pillow are required. Initial export requires an absent destination. `--check` rebuilds in memory and compares existing public files byte for byte without overwriting them. Cross-platform fonts and drawing dependencies may change image bytes; strict comparison applies to the same export environment.

```powershell
./.venv/Scripts/python.exe -B scripts/export_case_studies.py --check
```

Public readers can inspect the cases without the original run directories. Source hashes in `provenance.json` support traceability; they do not replace historical source snapshots or full model-experiment reproduction.

## Historical archive

- [pilot20_20261103_04](archive/pilot20_20261103_04/README.md): 10 early budget, execution, reporting, and quality-audit scripts; some entry points incur charges.
- [legacy_checks](archive/legacy_checks/README.md): old-receipt replay and fixed case84 reranking; neither calls a model, but both depend on historical files currently absent.

Archiving categorizes purpose; it does not establish compatibility with the current protocol or restore historical inputs. Archive maintenance adapted relocated project paths and added a main entry point for case84 reranking. Historical original source remains defined by frozen experiment snapshots. The public version also replaces private case-ID defaults in two validation scripts with explicit local selection parameters; see the archive guide.

## Test dependencies

Tests still call `economy_pilot.py`, `prepare_development_cases.py`, `validate_molecode_skill.py`, and archived `run_economy_validation.py`. They use synthetic data, temporary directories, and function-level checks without invoking paid archive entry points. See the [test guide](../tests/README.en.md) for the dependency mapping.

```powershell
./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider
```
