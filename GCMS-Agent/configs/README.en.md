# Configuration guide

**English** | [简体中文](README.md)

This directory contains the five YAML files required at runtime. Descriptions follow current source behavior; documentation maintenance does not change the model, scientific rules, or effective runtime limits.

## Files and loading order

| File | Purpose |
|---|---|
| [model.yaml](model.yaml) | Model connection, thinking settings, and historical price estimates |
| [chemistry.yaml](chemistry.yaml) | Fixed chemical constraints and experimental-condition descriptions |
| [scoring.yaml](scoring.yaml) | Compatibility declarations for the current scoring version |
| [runtime.yaml](runtime.yaml) | Base runtime parameters |
| [profiles/economy.yaml](profiles/economy.yaml) | Overrides for the default Agent profile |

Loading order: four base YAML files → profile overrides → explicit CLI arguments.

- `--profile standard` uses the base configuration directly. It does not read `profiles/standard.yaml`; that file is unnecessary.
- CLI `run` / `resume` in Agent mode default to `economy`; the `load_config()` function itself loads only the base configuration by default.
- `--mode baseline` uses the base configuration, makes no model calls, and does not apply economy overrides.
- Profiles update fields within each section, without recursive merging. An override of a nested field such as `model.prices_cny` must supply that field's complete contents.
- `--configs <directory>` selects a complete configuration directory containing all four base YAML files. Economy also requires `profiles/economy.yaml`. Missing files produce an explicit error rather than falling back to the project's own profile.
- CLI arguments override only fields with corresponding options, such as `--budget-cny` and `--strategy`; not every YAML field has a CLI option.

## Standard and economy

The table includes inherited values and code defaults. Reading only the economy file does not reveal the full effective configuration.

| Setting | standard | economy |
|---|---|---|
| Review strategy | `case_batch` | `case_batch` |
| Thinking | Enabled, `low` | Enabled, `low` |
| Maximum unique candidates per batch | 4 | 4 |
| Concurrent batches per case | 1 (code default) | 2 |
| Maximum rounds / cumulative time per candidate | 3 / 900 seconds | 3 / 900 seconds |
| SDK turns / submission reserve per session | 8 / 2 | 6 / 1 |
| Maximum analysis-tool calls per session | 2 | 4 |
| Session timeout | 300 seconds | 0, disabled |
| Effective-session limit per case | Unset | 8 |
| Start sessions only off-peak | No | Yes |
| Total / per-case monetary limit | Unset | Unset |
| Input / output token reservation estimate | 80000 / 65536 | 40000 / 8192 |
| Explicit output-token limit | `null` | `null` |

`standard` is not the older single-candidate mode. That mode requires `--profile standard --strategy legacy` together.

## Scheduling, budgets, and special values

- **Concurrency:** `concurrency` controls candidate processing in the legacy and baseline workflows; default batch review uses `batch_concurrency`. Different spectra are currently processed sequentially.
- **Batching:** `batch_size` limits unique candidates per batch. `max_batch_chars` splits batches by the total character count of compact candidate JSON, not token count or full request size. A single oversized candidate is not truncated.
- **Time:** `candidate_timeout_seconds` limits cumulative candidate processing time. `session_timeout_seconds` limits session duration and overrides `model.request_timeout_seconds`. Disabling the session timeout does not disable the candidate limit. The model-file timeout is not a separate HTTP connection timeout.
- **Rounds and proposals:** `max_rounds` and `stagnation_rounds` participate in stopping decisions. `proposals_per_round` is fixed at 3; the tool protocol also constrains proposals per candidate in batch review. SDK turns, analysis calls, alternative proposals, and elementary edit operations are different counts.
- **Output:** `max_output_tokens: null` uses CLI/service defaults, not unlimited output.
- **Reservations:** `reservation_input_tokens` and `reservation_output_tokens` estimate costs before session startup; they are not generation-length limits. When `max_output_tokens` is explicitly set, the output reservation instead uses `max_tool_turns × max_output_tokens`.
- **Diagnostic target:** `case_output_token_target` only marks reports; it does not truncate or stop generation.
- **Money:** `budget_currency` selects CNY or USD accounting. A `null` value for the corresponding `budget_cny` / `budget_usd` leaves the total monetary limit unset. `case_budget_cny` set to `null` leaves the per-case limit unset while retaining usage and cost records.
- **Sessions and failures:** Economy allows up to 8 effective sessions per case. Explicit connection failures with known zero usage and no tool execution may be exempt, but remain subject to `max_transport_failures_per_case`. Network retries must not be treated as unlimited free attempts.
- **Monitoring:** Economy enables stream monitoring, but pre-tool time, character, and response-count thresholds and the session character threshold are disabled with 0. `pre_tool_interrupt_grace_seconds` is the wait after an interrupt is triggered and must be greater than 0. A value of 0 does not have the same meaning for every field.

### Off-peak periods and pricing

The program uses Beijing time (UTC+8), treating Monday–Friday 09:00–12:00 and 14:00–18:00 as peak periods and all other times as off-peak. It does not query public holidays. This is the local scheduling and estimation policy in [budget.py](../src/gcms_agent/workflow/budget.py), not a guarantee of current provider policy.

With `offpeak_only: true`, peak periods block new sessions and record `offpeak_wait`. Explicit `resume` is required later; the program does not wait automatically and restart off-peak. The CLI `--offpeak-only` option can only enable this restriction. To disable it, use `offpeak_only: false` in a separate configuration directory and select a new run directory for the changed configuration.

USD and CNY prices in `model.yaml` are snapshots dated 2026-11-02 and 2026-11-03, respectively, per million tokens. This documentation update did not verify current prices or online model-alias availability. Estimated costs do not replace provider invoices.

## Fixed constraints and experimental conditions

The first four Boolean fields in `chemistry.yaml` must be `true` in this release. Setting them to `false` cannot relax heavy-atom composition, connectivity, neutrality, or closed-shell requirements.

The file's `ionization`, `energy_ev`, `energy_is_assumed`, and `resolution` fields are descriptive and are not currently read to inject or override sample conditions. Actual conditions come from input `conditions`. When absent from source data, preparation defaults to EI, an assumed 70 eV, and nominal mass resolution. Changing 70 in this configuration does not change the data or conditions received by the model.

`raw_linear` and depth 2 in `scoring.yaml` are compatibility declarations required by the loader. Actual enumeration reads fields such as `enabled` and `max_steps` from [rules.json](../resources/ei_rules/rules.json); `fragmentation_depth` does not override that file. See [CONDITIONS.md](../resources/ei_rules/CONDITIONS.md) for rule scope and the [scoring contract](../docs/scoring.en.md) for score definitions.

The removed `pilot_size` field was unused. Current preparation still produces up to 20 pilot samples. Runtime `--limit` restricts how many samples are read for a run without regenerating the dataset.

## Custom configuration

From the repository root, copy the complete configuration directory and edit the copy. Supply credentials through process environment variables or the root `.env`, not YAML.

```powershell
# Run only when the destination does not exist; keep custom settings for local experiments.
Copy-Item -LiteralPath ./configs -Destination ./runs/my_config -Recurse

# Explicitly select this checkout, avoiding editable installations pointing elsewhere.
$env:PYTHONPATH = (Resolve-Path ./src).Path

# After editing runs/my_config, run a baseline check without an API call.
./.venv/Scripts/python.exe -B -m gcms_agent.cli run data/raw_linear_v1/inference/pilot.json --mode baseline --configs runs/my_config --limit 1 --output runs/my_config_baseline
```

This example uses process-level `PYTHONPATH` to select this checkout when reusing an environment. It requires the existing project environment and local data. Fresh-environment setup is described in the project installation guide. When using economy with a custom directory, also check its `profiles/economy.yaml`, whose values override the base files.

## Changes and resumption

The loader checks ranges and fixed constraints for some fields, but is not a complete YAML Schema validator. Modify fields according to their documented use; lack of an error does not guarantee that a setting takes effect.

Resumption checks inputs, configuration, and source/resource fingerprints. Documentation and YAML comments under the project's `configs/` directory also participate in these fingerprints. Documentation maintenance may therefore make an old run incompatible even when effective parameters are unchanged. Use new output directories and retain original results and version checks. See the [resume contract](../docs/editing_and_stopping.en.md#resume) for permitted increases to total budgets and input-cost reservations.
