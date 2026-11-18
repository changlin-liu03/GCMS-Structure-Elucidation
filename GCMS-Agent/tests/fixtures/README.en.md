# Fixed instructional cases

**English** | [简体中文](README.md)

[molecode_skill_cases.json](molecode_skill_cases.json) contains 11 independently constructed MoleCode instructional cases for programmatic checks of graph syntax, structure reading, and editing boundaries. The file declares that it contains no experimental spectra, experimental evaluation labels, or LLM-generated expected answers.

## Field definitions

| Field | Purpose |
|---|---|
| `schema_version`, `purpose` | Fixture-format version and purpose declaration |
| `cases[].id` | Case ID corresponding to `<!-- case:... -->` in Markdown |
| `source_smiles` | Independent structural reference for the initial valid molecule, not experimental ground truth for an unknown sample |
| `document`, `title` | Associated instructional document and case name |
| `molecode` | Reference molecular graph with complete metadata |
| `initial_valid` | Expected initial validity; some cases deliberately contain errors |
| `facts` | Expected element composition, adjacency, rings, functional groups, isotope properties, or stereochemistry |
| `trials` | Operation parameters and expected validity, product structure, or rejection reason |

Three associated documents are in the [skill-example directory](../../skills/molecode-graph-reasoning/examples/):

- [topology.md](../../skills/molecode-graph-reasoning/examples/topology.md): branching, aromatic substitution, fused rings, functional groups, and isotope/EZ reading.
- [transaction.md](../../skills/molecode-graph-reasoning/examples/transaction.md): coordinated bond-order/hydrogen changes and repairs of unknown hydrogen counts and excessive valence.
- [repair.md](../../skills/molecode-graph-reasoning/examples/repair.md): aromatic-flag errors, ring reconnection, and rejected stereochemical edits.

The [validation script](../../scripts/validate_molecode_skill.py) reads molecular graphs and JSON operations from the actual Markdown and checks fixture consistency, graph round-trip fidelity, structural facts, and expected edit outcomes. Default pytest invokes the same function through an [integration test](../integration/test_molecode_examples.py).

Keep this file in the repository. It is neither Agent inference input nor a searchable spectral library. Skill examples have no observed peaks, so they cannot establish the structure or fragmentation mechanism of an unknown real sample. Passing these checks does not measure model comprehension accuracy.

When modifying instructional material, verify structural facts and expected outcomes first, then synchronize case IDs, document graphs, operations, and fixtures. Do not adopt current program outputs as expected answers without checking them.
