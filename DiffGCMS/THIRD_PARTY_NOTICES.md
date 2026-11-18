# Third-party sources and notices

DiffGCMS integrates the SpecTUS spectrum encoder with the DiffMS discrete graph diffusion decoder for GC–EI–MS molecular structure inference. The original encoder and decoder methods and implementations are credited to their upstream projects. This notice records component-level provenance; it does not claim that the upstream architectures were originally developed for DiffGCMS.

## Source code

| Component | Local integration points | Upstream source | Retained license |
|---|---|---|---|
| SpecTUS spectrum encoder | `src/models/gcms_encoder.py`; extraction and loading through `scripts/extract_spectus_encoder.py` | [hejjack/SpecTUS](https://github.com/hejjack/SpecTUS) | [SpecTUS MIT notice](licenses/SpecTUS-LICENSE.txt) |
| DiffMS graph decoder and diffusion pipeline | `src/models/transformer_model.py`, `src/models/layers.py`, `src/diffusion/`, and related training, metric and molecular graph utilities | [coleygroup/DiffMS](https://github.com/coleygroup/DiffMS) | [DiffMS MIT notice](licenses/DiffMS-LICENSE.txt) |
| DiGress graph diffusion foundations inherited through DiffMS | Graph Transformer, categorical diffusion and associated graph utilities | [cvignac/DiGress](https://github.com/cvignac/DiGress); [DiffMS upstream acknowledgement](https://github.com/coleygroup/DiffMS#readme) | [DiGress MIT notice](licenses/DiGress-LICENSE.txt) |

These paths identify integration areas, not a line-by-line authorship assignment. The root [LICENSE](LICENSE) retains the DiffMS copyright notice and MIT terms. No original copyright holder has been replaced with the DiffGCMS project name.

Upstream license sources, checked on 2026-11-18:

- [SpecTUS LICENSE](https://github.com/hejjack/SpecTUS/blob/main/LICENSE): Copyright (c) 2026 Masaryk University.
- [DiffMS LICENSE](https://github.com/coleygroup/DiffMS/blob/master/LICENSE): Copyright (c) 2025 Montgomery Bohde, Mrunali Manjrekar, Runzhong Wang, Shuiwang Ji, Connor W. Coley.
- [DiGress LICENSE](https://github.com/cvignac/DiGress/blob/main/LICENSE): Copyright (c) 2012-2022 Clement Vignac, Igor Krawczuk, Antoine Siraudin.

The local license files preserve the upstream copyright notices and full MIT terms; whitespace and line wrapping may differ. The dates above are source-check dates, not the dates when code was originally imported. The exact commits originally used for this experiment are not recorded in this repository; current branch links must not be interpreted as the original experiment revisions.

## Additional inherited attribution and dependencies

The `EigenFeatures` docstring in `src/diffusion/extra_features.py` retains a source attribution to [DGN's eigenvalue aggregation implementation](https://github.com/Saro00/DGN/blob/master/models/pytorch/eigen_agg.py). This attribution is preserved. No separate DGN license was identified on the inspected repository page or in that source file; this notice does not assign an MIT license to DGN or replace any applicable third-party terms.

The encoder imports BART layers from Hugging Face Transformers. Installed dependencies, including Transformers, PyTorch, PyG, Lightning and RDKit, retain their own licenses. Their upstream implementations are not made original DiffGCMS code by being imported here.

## Pretrained weights: separate terms

The default weight source used by `scripts/extract_spectus_encoder.py` is [MS-ML/SpecTUS_pretrained_only](https://huggingface.co/MS-ML/SpecTUS_pretrained_only), published by the MS-ML account. Its model card identifies the license as [Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0)](https://creativecommons.org/licenses/by-nc/4.0/), checked on 2026-11-18. The full terms are available in the [legal code](https://creativecommons.org/licenses/by-nc/4.0/legalcode.en).

The extraction script selects encoder tensors from the original model and writes them as local shards. This transformation does not grant an MIT license to the weights. The code license likewise does not automatically cover fine-tuned checkpoints containing or derived from those weights. Preserve the source attribution, license information and a description of changes when distributing covered material, and follow the noncommercial condition or obtain separate permission for uses outside the license.

No model weights are included in this repository. If a different local checkpoint is supplied through `--source`, its actual source and terms govern; the default model card does not establish the license of an arbitrary replacement checkpoint. Record the model revision and source used for each experiment.

## Data and scientific attribution

Datasets are not distributed with this repository and are not licensed by the root MIT file. Obtain and use each dataset according to its own terms.

Academic references are provided in [references.bib](references.bib) and the [README](README.md#citation). Citation acknowledges the research methods; it does not replace copyright notices, license terms or permission requirements. The SpecTUS bibliography entry deliberately identifies the 2025 v1 preprint, while the DiffMS entry cites the published ICML 2025 proceedings.
