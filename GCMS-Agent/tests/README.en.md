# Test guide

**English** | [简体中文](README.md)

This directory checks GC–EI–MS Agent chemistry, scoring, tool interfaces, and workflows. Default tests use synthetic samples, temporary directories, mock SDKs, or injected mock clients. They require no real API key, model calls, or full experimental dataset.

## Directory roles

| Directory | Coverage |
|---|---|
| [unit/](unit/) | Local behavior for molecular graphs, chemical constraints, EI rules, raw intensities, scoring, configuration, budgets, memory, and reporting |
| [integration/](integration/) | Tool protocols, Harness/SDK interactions, batch submissions, stream monitoring, and instructional-example consistency |
| [end_to_end/](end_to_end/) | Mock-model workflows, batch concurrency, resource termination, checkpoint resumption, and separate evaluation |
| [fixtures/](fixtures/README.en.md) | Fixed instructional structures and expected outcomes, not runtime inference inputs |

Some unit tests combine modules for regression checks. Directory names do not establish measured code coverage. `pytest` functions coexist with `unittest.TestCase` / `IsolatedAsyncioTestCase`; pytest collects them together.

## Running tests

Follow the [installation guide](../README.md#install-from-scratch) to create an isolated environment and install `.[test]` in editable mode. Run from the project root; `.venv` must have the required dependencies. `pyproject.toml` sets `pythonpath = ["src"]` and `testpaths = ["tests"]`. The commands below also explicitly select the process source path so standalone scripts use the same checkout.

```powershell
$env:PYTHONPATH = (Resolve-Path ./src).Path
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'

# Complete offline regression suite.
./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider

# Collection only; this does not establish that tests pass.
./.venv/Scripts/python.exe -B -m pytest --collect-only -q -p no:cacheprovider

# Check instructional documents against tools separately.
./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/integration/test_molecode_examples.py
```

The final path may be replaced with another test file or directory. Full dependency installation and platform support are maintained in the project installation guide. Passing locally does not establish validation on every operating system.

## Important tests and why they remain

| Test | Purpose |
|---|---|
| [test_chemistry.py](unit/test_chemistry.py) | Molecular graphs, SMILES/MoleCode round trips, heavy-atom constraints, and stereochemistry |
| [test_evidence.py](unit/test_evidence.py), [test_allylic_scope.py](unit/test_allylic_scope.py), [test_silicon_rules.py](unit/test_silicon_rules.py) | Fragmentation-rule scope, conservation, policies, and enumeration limits |
| [test_n_aromatic_diagnostics.py](unit/test_n_aromatic_diagnostics.py) | Prevents removed diagnostic channels from scoring again and checks ion/product validation; it is not disposable obsolete-feature testing |
| [test_nitrile_rules_and_exploration.py](unit/test_nitrile_rules_and_exploration.py) | Nitrile rules, equal/lower-score exploration, and historical-best preservation |
| [test_compact_views_v2.py](unit/test_compact_views_v2.py) | Fidelity of current compact model context; v2 in the filename does not imply retirement |
| [test_ranking_interpretation.py](unit/test_ranking_interpretation.py), [test_top1_report.py](unit/test_top1_report.py) | Tie selection, source counts, evidence limitations, and reporting |
| [test_config.py](unit/test_config.py), [test_credentials.py](unit/test_credentials.py) | Custom configuration directories and local credential-loading boundaries |
| [test_batch_tools.py](integration/test_batch_tools.py) | Trial references, candidate/version isolation, submission completeness, and stopping acceptance |
| [test_molecode_examples.py](integration/test_molecode_examples.py) | Integrates validation of actual instructional Markdown and JSON operations into default pytest |
| [test_recovery_and_evaluation.py](end_to_end/test_recovery_and_evaluation.py) | Version/input compatibility, budget resumption, persistence, and evaluation |

The legacy single-candidate mode remains supported, so its tests remain relevant. Passing establishes checked program behavior against assertions, not correct identification of unknown real samples or experimental confirmation of fragmentation mechanisms.

## Script and resource dependencies

Copying only `tests/` does not produce a standalone test bundle. Corresponding source code, configuration, skills, and rule resources are required.

| Test location | Dependency | Purpose |
|---|---|---|
| [test_economy_budget.py](unit/test_economy_budget.py) | [economy_pilot.py](../scripts/economy_pilot.py) | Label-free pilot selection and cost reporting |
| [test_validation_budget_report.py](unit/test_validation_budget_report.py) | [run_economy_validation.py](../scripts/archive/pilot20_20261103_04/run_economy_validation.py), [economy_pilot.py](../scripts/economy_pilot.py) | Shared historical-stage budgets, OS file locks, and cost reporting |
| [test_raw_intensity.py](unit/test_raw_intensity.py) | [prepare_development_cases.py](../scripts/prepare_development_cases.py) | Development-subset preparation with temporary synthetic data |
| [test_molecode_examples.py](integration/test_molecode_examples.py) | [validate_molecode_skill.py](../scripts/validate_molecode_skill.py), [instructional fixtures](fixtures/molecode_skill_cases.json), [skill examples](../skills/molecode-graph-reasoning/examples/) | Document graphs, structural facts, tool operations, and expected acceptance/rejection |

Historical budget tests call only budget and lock functions, not paid archive entry points. Preserve this dependency when reorganizing `scripts/archive/`; deleting scripts or their tests to hide failures is not a valid repair.

Model-integration tests replace the SDK with mocks; workflow tests inject mock clients. API-key strings in tests are placeholders. Temporary labels and results check isolation and evaluation logic, not real model effectiveness.

## Instructional cases and validation scope

The [fixture guide](fixtures/README.en.md) explains their purpose. There are currently 11 cases checked against three instructional documents through one integration test invoking the existing validation function. This is one pytest item, not 11 separate pytest items.

The standalone entry point remains available:

```powershell
./.venv/Scripts/python.exe -B scripts/validate_molecode_skill.py
```

When adding examples, establish structural facts and expected outcomes independently, then synchronize documentation and fixtures. Do not treat runtime outputs as ground truth without checking them or weaken assertions merely to make regression tests pass.

Real API availability, model-identification performance, usage, and costs require separate experimental evaluation. Counts of passing mock-call tests cannot substitute for those conclusions.

## Public case-export checks

[test_case_study_export.py](unit/test_case_study_export.py) uses temporary synthetic files to verify freeze hashes, path boundaries, sensitive fields, unknown usage, and atom-ID-aware deduplication. It also checks file hashes, object references, all trial counts, source slots, and incomplete statuses in public [case_studies/](../case_studies/README.en.md). It does not read private `runs/` or the full research dataset.

Maintainers with the two original run directories can also execute `scripts/export_case_studies.py --check` to reconstruct public bundles from frozen sources and compare them byte for byte. This source-record check is excluded from default pytest because original run directories are not distributed.

## Caches and maintenance

`__pycache__/`, `.pyc`, and pytest caches are regenerable artifacts, not test source. This directory does not store experiment logs or test outputs; tests write to temporary directories. Do not remove cases, skip tests without justification, or drop existing assertions merely to eliminate failures.
