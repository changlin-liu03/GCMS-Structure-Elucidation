# Spectral evidence, scoring, and evaluation contract

**English** | [简体中文](scoring.md)

The output `confidence_score` is a **0–100 score for rule-supported peak-intensity coverage**. It is neither the probability that a molecular structure is correct nor cosine similarity between a predicted and an experimental spectrum. A future probability field would require calibration experiments on independent development data to have statistical meaning.

## Input intensities

Current inputs use `input_ids` for integer nominal m/z and `intensity` for unbinned raw linear intensities, with `raw_linear` encoding. Input values are retained as `raw_intensity`. The model reads `intensity = raw_intensity = I`, while scoring uses `w_i = I_i / sum(I)`. Spectrum-reading intensities and scoring weights are distinct. The input maximum is not assumed to be 999, and small integer intensities are not guessed to represent bins.

Inputs must be nonempty arrays of equal length. Each m/z must be a unique positive integer. Intensities must be finite, nonnegative numbers with at least one positive value. Booleans, NaN, infinity, negative values, and duplicate m/z values are rejected. Zero signals remain in the inference source data but are excluded from observed peaks used for enumeration, scoring, and the model's spectrum table. All positive-intensity peaks are retained without weak-peak filtering, log transforms, or rounding.

`lower` / `upper` are compatibility fields and both equal the raw input intensity. No measurement-error model is provided; point values do not imply error-free experiments. Legacy binned `position_ids` inputs are explicitly rejected and must be prepared again from original intensity data into a new directory. Renaming bin IDs as linear intensities is not a valid conversion.

See the [data guide](../data/README.md) for input format and public synthetic examples. The maximum intensity or normalization convention of any particular dataset does not override the interface contract or establish that input intensities are absolute instrument counts.

## Evidence eligible for scoring

The [rule engine](../src/gcms_agent/fragmentation/rule_engine.py) enumerates paths using fixed templates. Parent molecules must pass strict chemical checks and be neutral, connected, and closed-shell. Molecular ions and subsequent ions may have odd-electron states, with independent element/hydrogen/charge accounting. Neutral RDKit fragments automatically completed with hydrogen after cleavage are not treated as ions.

The current [rules.json](../resources/ei_rules/rules.json) enables 16 rule IDs (`ei-conservative-1.3`). The table groups some related channels. Complete conditions, sources, and limitations of local extensions are documented in [CONDITIONS.md](../resources/ei_rules/CONDITIONS.md).

| Rule | Implemented scope |
|---|---|
| Molecular ion | A singly charged molecular radical-cation hypothesis |
| Alkyl sigma cleavage | All-carbon parent, specified saturated-carbon environments, and a single bond cut that separates components |
| Carbonyl/heteroatom-related alpha cleavage | Carbon-skeleton cleavage satisfying the implemented bond-environment and element conditions |
| Benzylic and allylic cleavage | Specified adjacent aromatic or alkene stabilization environments; the retained carbon at an allylic cleavage must be saturated in the parent, with all connected bonds single, excluding positions that themselves participate in double or triple bonds |
| Halogen radical loss | Applicable bond between saturated carbon and one halogen |
| M-H, M-H2 | **Listed channels for the ethane structure only**; not generalized to arbitrary C-H bonds |
| Alcohol dehydration | Simultaneous presence of a hydroxyl group, applicable alpha/beta carbons, and available hydrogens |
| McLafferty-type path | A carbonyl, an explicit alpha-beta-gamma chain, gamma hydrogen, and products satisfying conservation checks |
| CO loss from an acylium ion | A specific two-step path following carbonyl alpha cleavage |
| HCN loss from an alkenyl nitrile | A terminal nitrile attached to nonaromatic C=C, with beta-H and an acyclic double bond; records beta-H transfer and conversion of the double bond to a triple bond; a single-step template only |
| CN radical loss from an aryl nitrile | Aromatic carbon connected to a terminal nitrile by a single bridge bond; designated C/N atoms leave as a neutral radical, without hydrogen transfer |
| TMS methyl-radical loss | An explicit local trimethylsilyl environment, implementation-specific Si–C cleavage, and product checks; not generalized to all silicon-containing structures |
| TMS cation cleavage | An explicit TMS attachment environment, implementation-specific attachment-bond cleavage, and product checks; local extensions to N/S/saturated C remain template hypotheses |

`max_steps=2` does not mean that all two-step reaction sequences are supported. Apart from implemented two-step rules, arbitrary templates are not recursively combined. Arbitrary double ring cleavages, arbitrary HCN-loss chains, and unlisted rearrangements do not contribute to the score. Silicon-specific channels are limited to implemented TMS templates. Non-TMS silicon pathways, arbitrary hydrogen transfers, and unlisted rearrangements do not score merely because their masses match.

Template checks include the existence of the declared cleaved bond, element/hydrogen conservation across retained atoms and neutral products, total charge conservation, electron-parity consistency, and structural applicability. `verified=true` means these program checks passed; it **does not establish experimental confirmation of a fragmentation mechanism or ion abundance**.

Integer spectrum peaks are matched using nominal isotope masses, not by simply rounding exact masses. Exact ion m/z values in reports subtract electron mass; neutral-parent exact mass is calculated separately. M+1/M+2 are isotope-hypothesis annotations only, not independent bond-cleavage support or confirmation of isotope ratios.

The current path limit is 10000, counted before path deduplication and observed-peak matching. If generating another path would exceed the limit, the evidence interface returns `complete=false`. Truncated partial coverage is not used to rank the candidate normally. This is not a limit on the number of supported peaks.

## Scores and compatibility fields

Let `a_ij=1` when candidate j has at least one qualifying path matching observed peak i, and zero otherwise.

```
S_j = sum(w_i * a_ij)
confidence_score_j = 100 * S_j
S_lower = S_upper = S_j
```

Each peak contributes at most once. Multiple mechanisms explaining the same peak do not increase the score. Original input order, duplicate-candidate counts, diffusion-model scores, and the LLM's expressed confidence do not enter this calculation. The molecular-ion peak is already included in coverage and receives no additional weight. Unexplained peaks remain in the denominator.

Compatibility lower and upper bounds both equal the point score. The implementation does not compute bin intervals, bin sensitivity, interval competitors, or robust interval margins. Ranking reports retain highest-score ties, identical supported-peak sets, point-score gaps, and unexplained intensity. Measurement uncertainty is not modeled; uncertainty about structural identity and rule coverage remains.

Outputs include per-peak `weight`, `supported`, and `contribution`, plus `supported_peaks` and `unassigned_peaks`, allowing independent score recomputation. A valid structure with no supported peaks scores zero. A chemically invalid structure or one with incomplete evidence computation has a `null` score. These states must not be conflated.

## Edit acceptance

The first repair of an invalid parent into a structure satisfying the constraints may be saved, including at zero score, without requiring a match to a highest-intensity bin. Subsequent exploration from valid structures no longer requires score improvement or newly supported peaks. Chemical validity, heavy-atom composition, deduplication, and evidence completeness remain hard constraints.

The current exploration graph is separate from the historical best graph. Equal- or lower-scoring proposals may become the next round's starting point. Final output uses the highest historical point score, retaining the earlier structure on ties. Historical graphs are stored with validation, evidence, and graph versions; resumption continues from the saved exploration cursor. Accepted exploration is not necessarily an improvement in scientific evidence.

## Ranking and limitations

Final structures are merged by canonical isomeric SMILES. Identical structures must have identical scores, and all source records are retained. Structures are sorted by descending S; scores within `1e-12` are treated as tied. Highest-score ties use the source-count rule below to choose Top1; other ties retain a deterministic canonical SMILES display order. Zero-score candidates may be retained, but no definite Top1 is reported when all scores are zero. If all candidates fail, no structure is invented.

Coverage scoring tends to reward structures with many accessible fragments; it does not predict competing reactions or ion abundances. Exact structure-identification performance requires independent evaluation. The highest score alone does not establish a correct structure.

## Source counts for highest-score ties

When at least two distinct final structures share the highest evidence score, choose Top1 within that set by descending original source-candidate count. If counts also tie, use canonical SMILES lexicographic order. Each original candidate slot contributes one vote, including repeated inputs whose computation is shared. The number of independently computed candidates is recorded separately and does not replace source votes. A lower-scoring structure cannot outrank a higher-scoring one through votes.

The policy version is `top_score_source_count_v1`. Display order in `top1` and `unique_ranking` follows this policy; `rank`, `tied`, and `top1_candidates` still preserve evidence-score ranks and the highest-score tie set. Source votes do not change evidence scores or represent independent experimental support or identity probabilities. Acc@1 uses the selected `top1`, and MRR uses display-list position. The strict end-to-end treatment of incomplete runs remains unchanged. Historical frozen results retain their recorded ordering; reranking requires a separate derived directory.

## Evidence status and implementation

The [scoring function](../src/gcms_agent/scoring/support.py) emits per-peak contributions. The [ranker](../src/gcms_agent/scoring/ranking.py) merges identical final structures and preserves source slots. `identity_confirmed` remains false, and `measurement_uncertainty_modeled` indicates that measurement uncertainty is not currently modeled.

`molecular_ion_only` indicates support from the molecular ion alone, without fragment evidence distinguishing same-mass isomers. `majority_intensity_unassigned` describes more than half of the peak intensity remaining unexplained by the rules; it is not an additional edit-rejection threshold. Reports also list the strongest unexplained peaks. Path counts, protocol completion, and high coverage do not replace independent identity determination.

<a id="evaluation"></a>
## Independent evaluation

Inference receives only label-free inputs. Labels are joined by `sample_id` in a separate evaluation stage. Answers, post-hoc explanations, and prompts tuned using the answers must not be written back into skills, model inputs, or shared exploration summaries. Samples whose answers have been inspected for debugging are development samples and must not continue to be treated as untouched holdouts.

For comparisons, fix inputs and source ordering, rules, configuration, skills, and code version. Report the original candidate order, deterministic rule reranking, and Agent editing plus reranking separately. Candidate provenance and evaluation definitions must remain comparable across the three workflows. Baseline results must not be described as model-editing effects.

The [evaluator](../src/gcms_agent/evaluation/runner.py) reads saved results and separate labels. The general `evaluate` command does not automatically create or verify a prediction-freeze manifest. Strict experimental freezing requires recording prediction-file hashes and version information separately before loading labels. Calling the CLI alone does not replace experimental isolation procedures.

| Metric or reporting scope | Meaning |
|---|---|
| `Acc@1` | Uses the recorded `top1`; a null Top1 does not receive credit merely because the first displayed entry happens to match |
| `Acc@10`, `MRR` | Use unique-structure display order for Agent/rule results; MRR is the reciprocal of the first matching position, or zero if absent |
| Original-order metrics | Use original slot order; their @k definition differs from deduplicated unique-structure rankings |
| Output validity | Fraction of nonempty outputs passing strict chemical validation; null when there are no outputs |
| Repair rate and damaged-correct count | Track whether initially invalid slots become valid and whether initially correct slots become incorrect; validity is not identity correctness |
| `processed`, `complete_only` | Cover saved results and their complete subset, respectively, rather than all requested samples |
| `requested_set` | Includes unprocessed samples, counting missing results as misses; saved provisional rankings may contribute |
| `strict_end_to_end_requested_set` | Counts incomplete or unprocessed samples as misses, avoiding evaluation only on easy-to-complete samples |

Implementations are in [metrics.py](../src/gcms_agent/evaluation/metrics.py) and [baselines.py](../src/gcms_agent/evaluation/baselines.py). Preserve both source-slot counts and unique-structure counts. Do not fabricate candidates to compute Acc@10 when fewer than ten unique structures remain. Evaluate ties using recorded order, without optimistic tie credit.

A complete experimental report should also include processing rates, failure types, costs and unknown usage, timing definitions, and data splits. Small development samples, synthetic examples, and offline mock tests do not establish live-model accuracy or full-dataset cost. Any later fitting of scoring parameters or probability calibration should use independent development data, ensuring that spectra of the same molecule do not appear in both development and final-test groups.

Baseline and evaluation commands requiring no private data are provided in the [public examples](../data/examples/README.md). Raw inputs, labels, and individual results from the real dataset are not distributed with these technical documents.
