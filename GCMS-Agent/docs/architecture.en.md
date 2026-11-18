# Architecture and trust boundaries

**English** | [简体中文](architecture.md)

The Python host controls GC–EI–MS Agent execution. The model reads candidates, proposes hypotheses, and calls controlled tools. The program determines chemical validation, evidence enumeration, scoring, structure commits, budgets, and stopping states.

## Data flow

```text
Local raw data -> Trusted preparation -> Label-free inputs -> Python workflow
                       |                                     |-- Molecular graphs / validation / edits
                       |                                     |-- Fixed EI rules / scoring
                       |                                     `-- Harness -> Claude SDK/CLI -> Model service
                       |                                                                     <-> Controlled MCP tools
                       `-> Separate labels -> Post-run evaluator <- Saved predictions
```

[Data preparation](../src/gcms_agent/data/prepare.py) separates source data into inference inputs and labels and records source hashes, input hashes, and intensity encoding. The [input loader](../src/gcms_agent/data/loader.py) accepts only allowlisted fields and rejects unknown fields such as `true_smiles`. Private data remain local. The public [synthetic examples](../data/examples/README.en.md) are independently constructed and are not benchmarks based on measured spectra.

The [runner](../src/gcms_agent/workflow/runner.py) provides two main execution paths: `baseline` directly validates and scores candidates without model calls or structure edits; `agent` explores candidates through model-tool sessions. Different spectra are currently processed sequentially. Within a spectrum, equivalent candidates share computation while all original source slots are retained in the output.

## Model and tool boundaries

The [Harness client](../src/gcms_agent/harness/client.py) uses the Claude Agent SDK and native CLI to access the configured compatible model service. Model names, thinking settings, and cost parameters are described in the [configuration guide](../configs/README.en.md). Model identifiers returned by the service are recorded; an alias is not an immutable model-weight version.

SDK configuration disables default tools, automatic native-skill loading, personal settings sources, and session persistence. Only project-registered MCP tools are exposed. Each session has a separate working directory and `CLAUDE_CONFIG_DIR`. This limits the tools exposed to the model; it is not an operating-system process sandbox.

The entry point can load `DEEPSEEK_API_KEY` from a local `.env`; an existing process environment variable takes precedence. Credentials are excluded from inference inputs, model context, and source-version snapshots. The client also recursively rejects ground-truth and credential-like fields.

The default batch-review tools are registered in [batch_server.py](../src/gcms_agent/tools/batch_server.py):

| Tool | Purpose |
|---|---|
| `get_candidate` | Retrieve compact candidate state or the complete graph |
| `get_spectrum` | Retrieve raw linear peak intensities and separate full-spectrum weights |
| `get_evidence` | Retrieve complete fragmentation evidence for a candidate or trial |
| `read_reference` | Read a skill reference by registered ID; arbitrary file paths are not accepted |
| `analyze_proposals` | Apply edits to candidate copies, validate and score them, and issue trial IDs |
| `submit_reviews` | Submit structured reviews for all candidates in the batch by referencing trial IDs |

Final free text is not parsed as a structured result. Analysis trials are not automatically committed as candidate state; the workflow checks formal submissions again. Model reasons and tool evidence are stored separately. The program does not prove, operation by operation, the causal relationship between natural-language explanations and fragmentation mechanisms. See the [editing and stopping contract](editing_and_stopping.en.md).

## Skills, caches, and exploration information

[SkillLibrary](../src/gcms_agent/harness/skill_loader.py) explicitly loads five main skills and exposes reference documents on demand through registered IDs. Runtime prompts are generated from the [prompt-building code](../src/gcms_agent/harness/prompts.py) and current skills, without depending on exported static snapshots.

Compact model views retain MoleCode, all spectrum peaks, and their full-spectrum weights. Evidence paths use row tables with named columns, and repeated score objects are compressed through references. Query tools expose complete paths and atom IDs when needed. Fewer characters do not directly imply fewer tokens or lower cost.

Deterministic trials within a spectrum can be cached, but atom-mapped results are bound to the exact graph and atom IDs. The host manages each candidate's history, best graph, graph version, and trial records. Trial IDs from another session cannot directly support a new submission: the proposal must be analyzed again on the current graph and registered in the current session.

The [exploration summary](../src/gcms_agent/workflow/exploration.py) gives later batches information from previously completed batches for the same spectrum: valid structures, supported peaks, scores, ring information, and provenance summaries. It displays up to 40 structures, balancing high-scoring and recent structures, and records omitted counts. Sessions in the same concurrent batch group see the same prior state. The summary does not transfer atom mappings or trial IDs eligible for submission, and it does not guarantee that the model will avoid repeated exploration.

The standalone components in `memory/` are not connected to the default workflow. Candidate histories, trial caches, and same-spectrum exploration summaries are implemented by the workflow. Current-sample guesses and labels are not automatically written to a cross-sample knowledge base.

## Scheduling, usage, and reliability

The default Agent uses the `economy` profile, with up to two concurrent batches for one spectrum. After a concurrent batch group finishes, states are committed in a fixed order and checkpoints are saved. All candidates receive an initial review before the next round starts. Fatal errors stop new requests; requests already sent still require receipt persistence and usage settlement.

The [budget ledger](../src/gcms_agent/workflow/budget.py) accounts for final usage once per request. Shared-session costs must not be counted again for each candidate. An interrupted session without reliable final usage is marked unknown and retains an estimate. A zero-usage audit record does not automatically establish zero provider charges. Prices come from configuration snapshots, not live billing data.

Request attempts and effective-session counts are separate. An attempt may be exempt from the effective-session count only for specific auditable connection errors with known zero usage and no tool or trial execution. The receipt is retained, and a separate connection-failure limit applies. Missing receipts or unknown usage do not qualify for exemption. See [transport-error classification](../src/gcms_agent/harness/transport.py).

Neither the base configuration nor economy sets a default total monetary limit; economy also has no default per-spectrum monetary limit. Limits can be configured explicitly. Reserved cost, generation limits, SDK turns, analysis-call counts, and candidate rounds are distinct controls; see the [configuration guide](../configs/README.en.md). Known output-limit errors, service errors, and exhausted resources must not be interpreted as chemical convergence.

## Results and scientific limitations

Valid exploration permits equal or lower scores. The current exploration graph is stored separately from the historical best output. A nonempty `final_smiles` must meet strict parent-molecule constraints. Insufficient evidence and incomplete processing retain their respective statuses; see the [molecular contract](molecode_contract.en.md) and [scoring contract](scoring.en.md).

The model does not rewrite fixed rules or skills during inference. Rule-supported scores, protocol completion, and confirmation of structural identity are distinct concepts. Unimplemented fragmentation paths, isotope-ratio hypotheses, and model confidence cannot independently increase the score.

Runs record fingerprints of inputs, configuration, source code, skills, and rules. Resuming requires compatibility checks; exceptions and usage are described in the [resume contract](editing_and_stopping.en.md#resume).
