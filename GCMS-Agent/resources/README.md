# Runtime resources and historical provenance records

**English** | [简体中文](README.zh-CN.md)

This directory contains the EI rule policy actually read at runtime, documentation of rule applicability, and historical provenance/version records. These serve different purposes; check how each is loaded before modifying it.

## File purposes

| File | Purpose |
|---|---|
| [ei_rules/rules.json](ei_rules/rules.json) | Runtime rule policy selecting enabled templates and enumeration limits |
| [ei_rules/CONDITIONS.md](ei_rules/CONDITIONS.md) | Index, detailed conditions, sources, and capability boundaries for 16 rule types |
| [versions.json](versions.json) | Historical upstream file paths, SHA-256 hashes, and manually recorded dependency/implementation revision labels |

## Active rule policy

[load_rule_policy](../src/gcms_agent/fragmentation/rule_engine.py) reads `ei_rules/rules.json` in this directory by default. Rules are implemented in [rule_engine.py](../src/gcms_agent/fragmentation/rule_engine.py) and [silicon.py](../src/gcms_agent/fragmentation/silicon.py). Editing descriptive text does not change the chemical applicability conditions in code.

| Field | Current value or purpose | Modification boundaries |
|---|---|---|
| `version` | `ei-conservative-1.3`, recorded with evidence results | A version label; file hashes are also needed to identify actual content differences |
| `enabled` | 16 enabled rule IDs | Selects only rules registered in code; adding a string does not implement a new mechanism |
| `max_steps` | 2 | The loader permits 1 or 2; this does not allow arbitrary recursive combinations of templates |
| `max_paths` | 10000 | A positive integer limiting generated paths before deduplication and matching, not the number of peaks |
| `scope` | Overview of applicability | Descriptive content; does not replace program constraints |
| `not_implemented` | List of unimplemented mechanisms | Descriptive content, not a second set of enable/disable switches |
| `references` | Scientific background, sources of local templates, and limitations | Does not download literature or dynamically generate rules |

When the path limit is exceeded, enumeration reports `evidence_enumeration_limit`; the [evidence API](../src/gcms_agent/fragmentation/enumeration.py) returns `complete=false`. Partial enumeration or incomplete results must not be interpreted as a complete zero score. The existence of a rule path, conservation checks, and nominal-mass matching do not establish an experimentally confirmed mechanism or predict fragment intensities.

In [configs/scoring.yaml](../configs/scoring.yaml), `fragmentation_depth` is a compatibility declaration for the current configuration loader; it does not override `max_steps` here. Check configuration constraints, implementation, and validation results together when changing rules.

The default rule file is the policy actually read by the workflow. `--configs` selects the model and runtime configuration directory; it does not automatically select a rule file in that directory. Lower-level evidence functions have an explicit rule-path parameter. For rule ablation, explicitly supply or record the actual rule policy rather than only editing descriptive text.

## Historical records in versions.json

This file retains 10 `source_materials` entries and manually recorded version labels. No current program reader has been identified that uses these fields to install dependencies or select runtime behavior. It is neither a dependency lockfile nor an automatically generated inventory of the current environment.

- `source_materials.*.path` records local reference locations used during historical adaptation. `../MoleCode-main/...` is not a runtime dependency; users do not need that project in the parent directory.
- `source_materials.*.sha256` records original source hashes and should be retained with the historical materials. The directory cleanup checked their format only; it did not retrieve the corresponding upstream files to verify their contents. These hashes are not replaced with hashes of current locally adapted files.
- Values such as `rdkit`, `claude_agent_sdk`, and `claude_cli` belong to that record and are not guaranteed to equal the versions currently installed by a user. Installation dependencies are defined in [pyproject.toml](../pyproject.toml) and [requirements.lock](../requirements.lock).
- `score_version`, `molecode_skill_revision`, `review_protocol`, `ranking_reporting_revision`, `thinking_transport_revision`, and `execution_revision` are manually recorded revision labels, not runtime switches. For example, `explicit-disabled` within a label does not establish whether thinking is currently disabled; inspect the [model configuration](../configs/model.yaml) and [economy overrides](../configs/profiles/economy.yaml) for actual settings.
- Existing provenance records do not provide upstream commit/tag identifiers; do not guess them. See [provenance.md](../skills/molecode-graph-reasoning/references/provenance.md) for the historical MoleCode adaptation.

This documentation update preserves `versions.json` and `ei_rules/rules.json` as-is. Historical labels have not been rewritten as a new release version.

## Versions and recovery

The rule loader computes `policy_sha256` from the original JSON file bytes; evidence diagnostics record it as `rule_policy_sha256`. Even whitespace or line-ending changes can therefore change the rule-file hash.

Through [source_versions](../src/gcms_agent/workflow/runner.py), the host also generates run fingerprints for files in `src/`, `skills/`, `resources/`, and `configs/` and writes them to the experiment manifest. These are separate from the historical source hashes in `versions.json`. Raw policy-byte hashes and host file fingerprints also use different computations and should not be compared as if they were the same field.

Documentation in this directory participates in host fingerprints. Although this update preserves the rule policy, use a new run directory after the update and retain earlier experiment results and recovery version checks.

## Offline validation

Run from the project root, explicitly importing this checkout's source:

```powershell
$env:PYTHONPATH = (Resolve-Path ./src).Path
$env:PYTHONDONTWRITEBYTECODE = '1'
./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/unit/test_evidence.py tests/unit/test_allylic_scope.py tests/unit/test_nitrile_rules_and_exploration.py tests/unit/test_silicon_rules.py
```

These tests check structural conditions, conservation and boundaries, policy switching, path limits, and other behavior of implemented rules without calling a model. Literature links are retained as provenance records; this directory cleanup has not rechecked literature conclusions or external-link availability. Passing program tests does not establish experimental confirmation of a fragmentation mechanism.
