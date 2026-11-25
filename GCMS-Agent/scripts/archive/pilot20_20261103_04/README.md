# Archived pilot20 experiment scripts

**English** | [简体中文](README.zh-CN.md)

These scripts supported the cost, reliability, and 20-case experiments on November 3–4, 2026. On November 5 they moved here from `scripts/`. The move updated project paths, source packaging, and test references while preserving the budget, scoring, and acceptance logic.

| Script | Purpose |
|---|---|
| `run_economy_validation.py` | Run validation in stages under a shared budget |
| `continue_pilot20_cases.py` | Continue selected cases |
| `resume_pilot20_observed.py` | Resume a run with observation logging |
| `validate_reliability_v3.py` | Validate v3 reliability |
| `validate_submission_v31.py` | Validate v31 submissions |
| `run_pilot20_v31.py` | Run the v31 experiment on 20 cases |
| `report_pilot20.py` | Freeze predictions and generate an evaluation report |
| `report_pilot20_interrupted.py` | Report interim results for an interrupted run |
| `audit_pilot20_v31_quality.py` | Audit structures, fragmentation evidence, and ranking quality |
| `analyze_pilot20_v31_cost.py` | Break down costs and compare them with historical runs |

The scripts share a directory so they can import each other when run directly. Use the project CLI or the top-level `scripts/` entry points for routine work. The workflow does not run these archived scripts automatically. Some make paid model calls, so avoid invoking their main functions to check the archive.

The scripts refer to their original `data/` and `runs/` paths. The move preserved historical results, ledgers, pending settlements, checkpoints, and `tested_source.zip`. Use the frozen source snapshot for the exact code used in an experiment. Keep version checks enabled when resuming historical runs.

## Selecting private cases

The public scripts omit private case identifiers. For preflight checks and execution, pass three local identifiers to `validate_reliability_v3.py` via `--sample-ids`, in this order: round limit, output limit, invalid-structure repair. For `validate_submission_v31.py`, pass one identifier with `--sample-id`. Without a selection, these commands exit before reading data or ledgers, creating a run lock, or calling a model.

Evaluation with `--evaluate` restores the selection from the frozen inputs and rejects selection arguments. It still checks input, source, and script fingerprints. The parameterized scripts cannot override these checks to evaluate an old snapshot. Historical inputs are still required. For current public examples, use the project CLI.
