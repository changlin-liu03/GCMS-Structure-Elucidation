# Technical documentation

**English** | [简体中文](README.md)

This directory describes the current GC–EI–MS Agent implementation and its scientific limitations. Runtime behavior is loaded from source code, configuration, skills, and rule resources; this directory is not read as model prompts or configuration. It contains no private-data cases, historical experimental results, or old prompt snapshots.

## Reading order

| Document | Questions it addresses |
|---|---|
| [Architecture and trust boundaries](architecture.en.md) | How data flow, which tools the model can call, and how the host isolates labels and manages state |
| [MoleCode and valid-structure contract](molecode_contract.en.md) | Graphs awaiting repair versus valid molecules, editing capabilities, stereochemistry, and product-validation limits |
| [Edit acceptance and stopping](editing_and_stopping.en.md) | Analysis and submission of proposals, separation of exploration and best results, stopping, and recovery |
| [Scoring and evaluation](scoring.en.md) | Scoring raw linear intensities, resolving ties, and evaluating results independently |

These documents describe the repository implementation. Model statements, rule matches, and synthetic examples are not treated as experimental confirmation. Default values are defined by configuration files and their loaded values.

## Usage entry points

- [English homepage](../README.md) / [中文首页](../README.zh-CN.md): project purpose and execution.
- [Case studies](../case_studies/README.en.md): three authorized historical cases, including all trials and incomplete statuses.
- [Data guide](../data/README.en.md) and [public synthetic examples](../data/examples/README.en.md): input format, label isolation, and an offline workflow without private data.
- [Configuration guide](../configs/README.en.md): base configuration and economy overrides, monetary limits, timeouts, and scheduling.
- [Script guide](../scripts/README.en.md): offline utilities, local mock-service checks, and live-model entry points.
- [Test guide](../tests/README.en.md): synthetic samples, mock clients, and checks of instructional examples.
- [Skill guide](../skills/README.en.md): explicitly loaded host-managed skills and on-demand references.
- [Rule-resource guide](../resources/README.en.md): rule policies, applicability conditions, and sources.

The four technical documents above, the configuration, data, script, test, skill, and resource guides, and the public case-study narratives have English and Chinese versions. Runtime skill text, on-demand references, instructional examples, and original model explanations retain their recorded language, mostly Chinese.

## Validation and publication scope

Public examples and default tests can run without the private research dataset. Passing tests establishes that checked program behavior satisfies the assertions; it does not independently validate the Agent's real identification accuracy, full-dataset cost, or speed. Comparisons of live-model performance should freeze a common version and data split and report complete results under the [evaluation contract](scoring.en.md#evaluation).

Apart from the three separately authorized case-study bundles, private inputs, derived subsets, labels, experimental outputs, and historical research records are not public. This technical-documentation directory does not duplicate those data or grant permission to download or redistribute private data.
