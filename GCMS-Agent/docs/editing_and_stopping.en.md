# Edit acceptance and stopping conditions

**English** | [简体中文](editing_and_stopping.md)

This document first describes the default `case_batch` workflow. Configuration defaults and overrides are covered in the [configuration guide](../configs/README.en.md). Differences in the older `legacy` mode are described at the end.

## Trials and formal submissions

The model receives the current exploration graph and its `graph_version`, which may differ from the historical best graph. Each candidate has its own atom IDs, graph version, and state.

One `analyze_proposals` call accepts up to three alternatives per candidate. A session may make multiple calls, subject to session limits such as `max_trial_batches`. Each candidate may ultimately reference up to three trials in `submit_reviews`. There is no fixed upper limit on the number of elementary edit operations within a proposal. These limits do not mean that an entire session may try only three proposals.

Analysis parameters include `candidate_id`, `base_version`, and `operations`; the version must match the current candidate. A submitted proposal reference contains only `trial_id`, `reason`, `peak_refs`, and `operation_explanations`. The host retrieves and checks operations from the current session's trial records; raw operations are not submitted again.

```text
Check version -> Apply the complete edit group to a graph copy -> Strict chemical and round-trip checks
              -> Fixed-rule enumeration -> Point-score calculation -> Issue trial ID
              -> Formal reference submission -> Host checks and deduplication -> Select new exploration state
```

Trials in the same batch use the corresponding candidate's current snapshot and do not themselves change the candidate graph. Every elementary operation requires a nonempty explanation, and peak references must come from the observed spectrum. Valid fields do not establish that the program has proved the mechanisms described in those explanations. See [batch_server.py](../src/gcms_agent/tools/batch_server.py) for the structured tool contract.

## Acceptance and the historical best

The host checks composition, chemical validity, previously seen structures, and evidence status in sequence. See the [acceptance function](../src/gcms_agent/scoring/acceptance.py) and [batch-review workflow](../src/gcms_agent/workflow/case.py).

- First valid repair: if the old graph fails final constraints, a new graph that passes them may be saved as a valid version without requiring a positive score. If evidence computation fails, the process still ends with `tool_error` and produces no normal score; validity must not be confused with complete evidence.
- Exploration from a valid graph: a different, unseen structure satisfying the constraints may become the next round's starting point once evidence enumeration is complete. Equal or lower scores are allowed; newly supported peaks or a fixed score gain are no longer required.
- Multiple accepted proposals: prefer proposals with scores, then select one for commitment by descending point score, descending compatibility lower bound, and ascending canonical SMILES. The current lower bound equals the point score. Rejected proposals and the original graph record are retained.

[Best-state management](../src/gcms_agent/workflow/search_state.py) separately stores valid structures that have been formally accepted. Complete scoring takes priority, followed by the highest point score; exact ties retain the earlier structure. At termination, the best state is output and the exploration cursor is stored separately for permitted recovery. Unsubmitted trials do not automatically become the best output.

## Complete review and stagnation

The host determines `review_complete`. It requires a valid `keep`, no edit proposals in the current round, completion of four checks, and either a complete evidence trial for a different valid structure or verifiable historical exploration. There must also be no qualifying unsubmitted trial that improves the historical best score.

The four checks are `graph_read`, `valence_checked`, `spectrum_checked`, and `alternatives_checked`. Historical exploration must be verified against the current graph, spectrum, and rules. A model's verbal claim of prior exploration is insufficient.

Batch review increments the stagnation count when no new structure is accepted and complete exploration evidence is present. Acceptance of a new structure resets the count; insufficient exploration also resets it. Zero trials, only invalid trials, or repetition of only the current structure produce `exploration_insufficient` feedback while remaining subject to round and time limits. `stop_review` is retained only for compatibility with older prompts. A stop request that fails host conditions is ignored.

## Stopping states

| Reason | Meaning |
|---|---|
| `objective_ceiling` | A valid structure reaches coverage score 1, using the numerical tolerance in code; this does not confirm a unique identity |
| `review_complete` | Batch-review protocol and exploration acceptance conditions are met |
| `stagnation` | The consecutive-stagnation threshold is reached; default: 2 rounds |
| `max_rounds` | The candidate-round limit is reached; default: 3 rounds |
| `timeout` | A candidate or request time limit is reached; default cumulative candidate limit: 900 seconds |
| `format_retry_exhausted` | The format-repair allowance is exhausted |
| `turn_limit`, `output_limit` | Session resources end without sufficient formal submissions |
| `budget_exhausted`, `case_budget_exhausted` | An explicitly configured monetary limit is reached |
| `case_session_limit`, `case_transport_failure_limit` | The spectrum's effective-session allowance or exempted connection-failure limit is reached |
| `offpeak_wait` | New sessions are not allowed in the current period; explicit resumption is required later |
| `tool_error`, service/authentication/billing errors | Execution fails; available state and the corresponding diagnostics are saved |

Stopping conditions are enforced jointly by the [candidate termination function](../src/gcms_agent/workflow/termination.py), workflow, and session controls. Candidate structure status and stopping reason are recorded separately: for example, `corrected` and `max_rounds` may coexist. A chemically invalid structure has `final_smiles` set to `null`. A valid structure may be retained when resources end before completion, while its ranking is marked incomplete.

If an output or turn error occurs after a verified submission, the submission may be retained only when final usage is known and the client's recovery conditions are met. Resource labels do not override service errors. Network failures and format errors must not be presented as completed chemical-exploration rounds.

## Session and monetary limits

Default economy sessions allow at most 6 SDK turns, with 1 reserved for submission, and at most 4 analysis calls. Queries, submissions, and control logic may reduce the actual number of analysis calls. A spectrum allows at most two concurrent batches and defaults to 8 effective sessions. See the [configuration guide](../configs/README.en.md) for the meaning of each count.

Total and per-spectrum monetary limits are unset by default. They are enabled only through `--budget-cny` or the corresponding configuration. Cost reservations are scheduling estimates, and unknown usage retains an estimate; these are not hard provider billing caps. `max_output_tokens: null` uses CLI/service defaults rather than allowing unlimited output. `offpeak_only` stops new sessions during peak hours and saves progress; it does not schedule automatic background resumption.

<a id="resume"></a>
## Checkpoints and resumption

The [runner](../src/gcms_agent/workflow/runner.py) requires compatible input hashes, run mode, backend, scientific configuration, and source/skill/resource fingerprints when resuming. Checkpoints store graph versions, exploration history, best states, and receipts. Usage settlement and request identifiers help avoid duplicate accounting on replay. Do not edit manifests or remove version checks to force continuation of incompatible results.

Total run budgets and input-token reservation estimates have explicit adjustment paths. Input reservations may remain unchanged or increase. Existing total budgets may only increase in ways supported by the runner; a previously unlimited run cannot become limited during resumption. Per-spectrum caps and other scientific configuration remain part of the consistency check. These exceptions do not permit arbitrary configuration changes.

Source fingerprints cover text files in `src/`, `skills/`, `resources/`, and `configs/`. Documentation or comment changes in those directories may also change fingerprints. Use a new run directory for a new scientific configuration.

## Legacy mode and baseline

`legacy` reviews each candidate independently. It lacks the four default batch-review checks and shared exploration acceptance. Its stagnation counter increases whenever no proposal is accepted. The batch-review `review_complete` conditions must not be applied to the legacy mode. See [candidate.py](../src/gcms_agent/workflow/candidate.py).

`baseline` performs no model-driven editing and stores deterministic results as `baseline_only`. Candidate chemical failure and incomplete model processing are different states. Metric definitions are described in [scoring and evaluation](scoring.en.md#evaluation).
