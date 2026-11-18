# Agent skill directory

**English** | [简体中文](README.md)

This directory contains runtime prompt materials for the GC–EI–MS Agent, explicitly loaded by the Python host. It is not a plugin package that users must install separately in a personal assistant directory.

## Skill responsibilities

| Skill | Responsibility | Supporting materials |
|---|---|---|
| [gcms-input-audit](gcms-input-audit/SKILL.md) | Input, raw peak intensities, and metadata boundaries | No separate reference files |
| [molecode-graph-reasoning](molecode-graph-reasoning/SKILL.md) | Molecular graph syntax, adjacency and structure reading, and editing boundaries | 3 references, 3 instructional examples, and an upstream license |
| [ei-fragment-evidence](ei-fragment-evidence/SKILL.md) | Rule-supported evidence, conservation, and unexplained mechanisms | Evidence boundaries and silicon-rule boundaries |
| [constrained-structure-edit](constrained-structure-edit/SKILL.md) | Trials, committed changes, acceptance, and stopping | Batch-review protocol, acceptance, and stopping |
| [rerank-and-explain](rerank-and-explain/SKILL.md) | Scores, tied Top1 results, and explanation boundaries | No separate reference files |

## Loading and invocation

[SkillLibrary](../src/gcms_agent/harness/skill_loader.py) loads the five fixed `SKILL.md` files above. Their full contents are incorporated into the system prompt by [prompts.py](../src/gcms_agent/harness/prompts.py). YAML front matter is included as part of the file text; it is not an automatic discovery or routing mechanism.

Markdown files directly inside each skill's `references/` and `examples/` directories are registered as materials available through `read_reference`. There are currently 10 such files. Calls use the full `reference_id` relative to `skills/`, for example `molecode-graph-reasoning/references/syntax.md`. Reference bodies are not all placed in the prompt in advance; the model can read them on demand using the published IDs. Preserve existing directory and file names to avoid breaking references.

Neither directory README nor `UPSTREAM_LICENSE.txt` is loaded as skill text or a `read_reference` resource. The host's run fingerprints still cover files in `skills/`, so documentation maintenance also changes version fingerprints.

## Tools and scientific boundaries

- Default batch review uses `analyze_proposals` to test copies and `submit_reviews` to commit changes. Each analysis call accepts at most three alternative proposals per candidate. A session can make multiple analysis calls within its allowance; the final submission accepts at most three trial references per candidate. There is no limit on the number of atom-edit operations within an individual proposal.
- Legacy single-candidate mode uses `validate_proposal` / `analyze_proposal` / `submit_review`. Instructional JSON shows operation arguments; batch calls require the outer structure described at the beginning of each example. The tool schema in the current session is authoritative.
- The score is rule-supported raw-intensity coverage, not the probability that an identity is correct. When the highest scores are tied, the number of source candidates helps select the displayed Top1; it neither increases the evidence score nor removes the tie.
- Accepting a new valid exploration structure does not require an improved score. The host stores the current exploration graph and the historical best result separately. Actual acceptance, ranking, and stopping are determined by the program; skill text does not replace program checks.
- Instructional structures contain no experimental spectral peaks and supply no mass-spectral evidence for the current case. Do not put experimental answers or evaluation labels into skills.

## Provenance and maintenance

See [provenance.md](molecode-graph-reasoning/references/provenance.md) for MoleCode's historical sources and local adaptations, and [UPSTREAM_LICENSE.txt](molecode-graph-reasoning/UPSTREAM_LICENSE.txt) for the retained upstream license. Historical local reference directories are not runtime dependencies. Do not guess missing upstream commit/tag identifiers.

When maintaining prompts, check tool fields, score semantics, proposal limits, and stopping conditions together. Do not translate runtime skills solely to make documentation languages uniform: changes to skill bodies or on-demand references may affect model behavior and should be validated separately from translations of ordinary project documentation. This English guide describes the existing skills; linked runtime skill bodies, references, and instructional examples retain their original language.

## Offline validation

Run from the project root, explicitly using this checkout's source to avoid importing another project copy from an existing environment:

```powershell
$env:PYTHONPATH = (Resolve-Path ./src).Path
$env:PYTHONDONTWRITEBYTECODE = '1'
./.venv/Scripts/python.exe -B scripts/validate_molecode_skill.py
./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/integration/test_harness.py tests/integration/test_batch_tools.py tests/unit/test_ranking_interpretation.py tests/unit/test_top1_report.py tests/unit/test_nitrile_rules_and_exploration.py tests/end_to_end/test_case_batch.py
```

The instructional validator reads molecular graphs and JSON from three example documents. It checks structural facts, round-trip fidelity, and expected acceptance/rejection outcomes for 11 cases without calling a model or reading experimental labels. Passing these checks establishes compatibility between the instructional materials and tools; it does not establish improved model comprehension or identification accuracy.

Use a new run directory after changes and retain original results and version checks for earlier experiments. Existing output prompt snapshots are not updated automatically; runtime prompts are assembled from the actual source and contents of this directory.
