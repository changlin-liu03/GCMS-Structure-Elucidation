# GCMS Structure Elucidation

English | [简体中文](README.zh-CN.md)

Molecular structure elucidation from GC–EI–MS spectra through diffusion-based candidate generation and agent-guided structure refinement, reranking, and fragmentation interpretation.

This repository contains two complementary research modules:

| Stage | Module | Role |
|---|---|---|
| Candidate generation | [DiffGCMS](DiffGCMS/README.md) | Combines a SpecTUS spectrum encoder with a discrete molecular graph diffusion decoder derived from DiffMS. Conditions on the spectrum and supplied heavy-atom types and counts to generate candidate bonds. |
| Candidate review and interpretation | [GCMS-Agent](GCMS-Agent/README.md) | Reviews ten candidate SMILES against the original EI spectrum, proposes structure edits under a heavy-atom composition constraint, and uses chemistry tools to validate, score, rank, and explain candidates. |

The modules have separate environments and entry points. Data preparation is required between stages; this release does not provide a single command that runs both stages end to end.

## Repository layout

```text
GCMS-Structure-Elucidation/
├── DiffGCMS/       # Graph diffusion model, training and candidate generation
└── GCMS-Agent/     # Candidate review, tools, tests and public case studies
```

Clone the repository:

```bash
git clone https://github.com/changlin-liu03/GCMS-Structure-Elucidation.git
cd GCMS-Structure-Elucidation
```

Run each module's commands from its own subdirectory. References to “repository root” in module documentation mean that module's directory, including in linked guides. Install the modules in separate Python environments.

## Getting started

**DiffGCMS:** Run `cd DiffGCMS` from the repository root, then follow the [installation and training instructions](DiffGCMS/README.md). The declared Python minimum is 3.9. Prepare compatible PyTorch dependencies, your dataset and statistics, the SpecTUS encoder weights, and the appropriate trained checkpoint for inference. Research datasets and model weights are not included.

**GCMS-Agent:** Run `cd GCMS-Agent` from the repository root, then follow the [installation and public example instructions](GCMS-Agent/README.md). The documented verified environment is Windows x86-64 / Python 3.10.19. Editable installation from the complete module directory is required for access to its configs, skills and resources.

- `baseline` validates, scores, and ranks candidates without a model API key.
- `agent` uses a configured model service to review and edit candidates; it requires credentials and can incur service charges.
- Keep credentials in `GCMS-Agent/.env` or the process environment. Only the placeholder `.env.example` is published.

## Passing candidates between stages

DiffGCMS predictions and the current Agent input schema differ. See the [DiffGCMS data specification](DiffGCMS/docs/data.md) and [Agent data specification](GCMS-Agent/data/README.md).

| Field | DiffGCMS prediction export | Current Agent input |
|---|---|---|
| Spectrum | `input_ids` and binned `position_ids` | `input_ids` and original linear `intensity` |
| Candidates | `generated`: objects with `smiles`, `inchi`, and `valid` | `generated`: exactly ten nonempty SMILES strings |
| Identity | The compact export does not include `sample_id` | A unique safe ASCII `sample_id` is required |
| Reference structure | `true_smiles` may be included | Store labels separately; inference input rejects `true_smiles` |

Use a reliable sample mapping to recover the corresponding original linear spectrum, extract the candidate SMILES, and assign a unique sample ID. Handle missing candidates explicitly before validation: null candidates do not meet the Agent input contract. Binned `position_ids` do not recover the original linear intensities and must not be renamed to `intensity`. Remove unsupported fields from inference inputs. Reference labels belong only in a separate evaluation file.

The Agent's data preparation utility expects an already compatible source schema; it is not a direct converter for the DiffGCMS prediction export. No automatic adapter is included in this release.

## Examples and validation scope

- [Synthetic examples](GCMS-Agent/data/examples/README.md) demonstrate the offline Agent workflow without private data or model calls.
- [Two public historical case studies](GCMS-Agent/case_studies/README.md) preserve inputs, structure edits, results and provenance. Their main Agent runs were incomplete; they illustrate development trajectories, not current-version accuracy.
- The existing GCMS-Agent release audit records 398 passing offline tests and successful baseline runs on two synthetic examples in its documented environment. This is a recorded module validation, not a new end-to-end validation of the two-stage workflow.
- Full DiffGCMS training and inference require external data and weights. They were not rerun for this combined repository publication.

## Sources, licenses, and data

Each module retains its own license and third-party notices:

- DiffGCMS: [LICENSE](DiffGCMS/LICENSE), [third-party notices](DiffGCMS/THIRD_PARTY_NOTICES.md), and [research references](DiffGCMS/references.bib).
- GCMS-Agent: [LICENSE](GCMS-Agent/LICENSE) and [third-party notices](GCMS-Agent/THIRD_PARTY_NOTICES.md), including SDK and MoleCode provenance.

These files describe the scope of the included code and retained upstream notices. Model weights, external datasets and the two historical case-study bundles have separate terms or exclusions described in the module documentation; the code licenses do not relicense those materials. Private research datasets, credentials, local environments, raw runs and generated outputs are excluded from this repository.

For research use, retain the applicable upstream acknowledgements and follow the citation guidance in each module.
