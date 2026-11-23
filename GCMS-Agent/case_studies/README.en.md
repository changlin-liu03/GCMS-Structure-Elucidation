# Public case studies

**English** | [简体中文](README.md)

The data holder explicitly authorized publication of these three real historical cases as a limited exception for private research data. Other data remain private; this directory does not grant permission to distribute the full dataset.

| Case | What it illustrates | Historical result boundaries |
|---|---|---|
| [Hexa-2,4-dienedinitrile](hexa-2,4-dienedinitrile/README.en.md) | Multi-round edits, equal/lower-score exploration, and restoration of the historical best | Historical scoring used binned intensities; saved Top1 matches, but the run is incomplete |
| [Butyl butanoate](butyl-butanoate/README.en.md) | Initial service failure, retry, and generation of the reference structure | Reference is second in display order; the retry is also incomplete |
| [Trimethylpentylsilane](trimethylpentylsilane/README.en.md) | Silicon-containing structure edits, tied scores, and source ordering | Reference is sixth in display order and tied for first by evidence rank; the run is incomplete |

Start with each case's Chinese or English README, then inspect its figure and JSON files. English versions translate the editorial process description; model summaries, reasons, and operation explanations remain in their original language and are labeled as original records. Across the three main Agent stages there are 24 model sessions with review receipts and 62 tool trials. The initial service failure in Butyl butanoate is recorded separately. Every trial is retained, including rejected and unselected trials; equivalent source slots share one computation history.

These public bundles are extracted from frozen records, not resumable checkpoints. Records cover initial graphs, operations, individual explanations, committed changes, evidence, scores, final rankings, failures, and usage. Model explanations are not confirmed mechanisms; hidden reasoning text is neither displayed nor reconstructed. Current code may differ from historical versions. No model was rerun and no score was recalculated for this presentation.

## File contract

Each case contains README.md, README.en.md, input.json, reference.json, trajectory.json, result.json, provenance.json, and trajectory.png. The trajectory shares graphs, scores, and evidence paths by SHA-256; every reference resolves within the same JSON file's objects pool. Each provenance file contains byte checksums for the other seven public files, source hashes, and frozen-inventory verification records. It does not establish that unpublished source files are independently available to readers.

All three display inputs use raw linear intensities. For hexa-2,4-dienedinitrile, these values come from `Agent-Harness/test.json`. Its trajectory, scores, and ranking come from the historical binned-intensity run. Evaluation labels are stored separately in reference.json.

## Local re-export

Export is for maintainers who possess the corresponding original runs. Export requires the project Python dependencies, Pillow, and the raw intensity source. The default source path is `../../../Agent-Harness/test.json` relative to the project root. Use `--raw-source` to specify another location.

```powershell
# First export: the destination directory must not exist.
./.venv/Scripts/python.exe -B scripts/export_case_studies.py
# Existing bundle: rebuild in memory and compare every file without overwriting it.
./.venv/Scripts/python.exe -B scripts/export_case_studies.py --check
```

Figures use local RDKit/Pillow versions and fonts; rendered bytes may differ across environments. The byte comparison performed by --check applies to the same export environment. Public readers can inspect all presentation materials without the original runs; see each case README for reading instructions. For a demonstration of current software without private data, see the [synthetic examples](../data/examples/README.en.md). Technical contracts are in the [documentation index](../docs/README.en.md).
