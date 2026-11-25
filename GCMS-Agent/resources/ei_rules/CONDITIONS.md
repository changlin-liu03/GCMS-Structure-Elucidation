# EI rule scope and provenance

Version: `ei-conservative-1.3`. These are finite, qualitative graph templates,
not a reproduced CFM-EI predictor, a thermodynamic calculation, or evidence that
one mechanism uniquely generated an observed peak. A match means a template has
the required parent environment and conserved products at the nominal m/z.
No rule predicts intensity. The absence of a match does not rule out a molecule.

The implementation is in [rule_engine.py](../../src/gcms_agent/fragmentation/rule_engine.py)
and [silicon.py](../../src/gcms_agent/fragmentation/silicon.py);
[rules.json](rules.json) selects enabled templates and resource limits. Editing
descriptive text cannot override the program's chemical conditions. Resource
provenance and version-record usage are explained in the [directory README](../README.md).

## Rule index

The current policy enables all 16 registered templates. The 12 core templates
and the four later nitrile/TMS additions are indexed here; their detailed
conditions, references and limitations remain in the sections below.

| Rule ID | Detailed scope |
|---|---|
| `molecular_ion` | [Core templates](#core-templates) |
| `alkyl_sigma` | [Core templates](#core-templates) |
| `carbonyl_alpha` | [Core templates](#core-templates) |
| `heteroatom_alpha` | [Core templates](#core-templates) |
| `benzylic` | [Core templates](#core-templates) |
| `allylic` | [Core templates](#core-templates) |
| `halogen_radical_loss` | [Core templates](#core-templates) |
| `hydrogen_radical_loss` | [Core templates](#core-templates) |
| `hydrogen_molecule_loss` | [Core templates](#core-templates) |
| `alcohol_dehydration` | [Core templates](#core-templates) |
| `mclafferty` | [Core templates](#core-templates) |
| `acylium_co_loss` | [Core templates](#core-templates) |
| `vinyl_nitrile_hcn_loss` | [Nitrile templates](#added-nitrile-templates-2026-11-11) |
| `aryl_nitrile_cn_loss` | [Nitrile templates](#added-nitrile-templates-2026-11-11) |
| `tms_methyl_radical_loss` | [TMS direct-cleavage templates](#tms-direct-cleavage-templates-2026-11-16) |
| `tms_cation_cleavage` | [TMS direct-cleavage templates](#tms-direct-cleavage-templates-2026-11-16) |

## Enumeration policy and limits

- `enabled` selects registered implementations. Adding an unknown ID to JSON
  does not create a new chemical rule; the loader rejects unknown IDs.
- `max_steps=2` permits implemented paths of up to two steps. It does not
  recursively combine arbitrary templates or add unimplemented reactions.
- `max_paths=10000` limits emitted paths before exact path deduplication and
  observed-peak matching. It is not a limit on the number of supported peaks.
- If emitting another path would exceed the path budget, enumeration raises
  `evidence_enumeration_limit`. The evidence API returns `complete=false`;
  partial coverage cannot be treated as a complete score or a complete
  zero-support result. See [enumeration.py](../../src/gcms_agent/fragmentation/enumeration.py)
  and the path-budget checks in [test_evidence.py](../../tests/unit/test_evidence.py).
- `version` is the policy label recorded with evidence. The original policy
  file bytes are also hashed as `policy_sha256` (reported as
  `rule_policy_sha256` in evidence diagnostics); formatting-only changes can
  therefore change the recorded policy hash.

## Core templates

| Rule ID | Implemented condition and products | Source / scope |
|---|---|---|
| `molecular_ion` | Neutral, connected, closed-shell parent loses one electron; +1 radical cation retains every atom and H. | [CFM-EI paper](https://doi.org/10.1021/acs.analchem.6b01622), EI background; no predicted molecular-ion intensity. |
| `alkyl_sigma` | C-only parent; single C-C bridge; retained carbon is saturated and nonaromatic. Retained alkyl cation plus complementary neutral radical. | [EI alkane study](https://doi.org/10.1016/S1044-0305(94)00095-H), class background; no claim that every permitted bridge produces an abundant ion. |
| `carbonyl_alpha` | Cleave C-C bridge at a carbonyl carbon, retaining C=O; acyl cation plus complementary radical. | [OpenStax carbonyl section](https://openstax.org/books/organic-chemistry/pages/12-3-mass-spectrometry-of-some-common-functional-groups). |
| `heteroatom_alpha` | C-C bridge adjacent to a neutral nonaromatic O/N/S donor on the retained side; valence at most 2/3/2 respectively. Hypervalent sulfur and positively charged donors are excluded. | [OpenStax alcohol/amine section](https://openstax.org/books/organic-chemistry/pages/12-3-mass-spectrometry-of-some-common-functional-groups); divalent sulfur is a qualitative lone-pair stabilization extension, not an intensity model. |
| `benzylic` | Single bridge from a nonaromatic carbon next to an aromatic atom; retained benzylic cation. Aromaticity is perceived on the validated parent even when MoleCode is Kekule. | [NIST ethylbenzene ion energetics](https://webbook.nist.gov/cgi/cbook.cgi?ID=C100414&Mask=20), representative class evidence; no unique tropylium connectivity asserted. |
| `allylic` | Single bridge from a neutral, nonaromatic **saturated carbon (all its parent bonds single)** adjacent to C=C; retained allylic cation plus a neutral radical. Retention sites participating in double/triple bonds are excluded; no generic ring two-cut search. | [IUPAC allylic site definition](https://goldbook.iupac.org/terms/view/A00245); [NIST 1-butene ion energetics](https://webbook.nist.gov/cgi/cbook.cgi?ID=C106989&Mask=20), representative fragmentation class. |
| `halogen_radical_loss` | Terminal F/Cl/Br/I attached by single bond to saturated carbon; retain the carbon skeleton cation and lose the halogen radical. | [NIST chloroethane ion energetics](https://webbook.nist.gov/cgi/cbook.cgi?ID=C75003&Mask=20), representative class; isotopic abundance is not inferred from this cleavage. |
| `hydrogen_radical_loss` | **Ethane motif only:** two carbons, one single bond, three H per carbon. Remove a named carbon's H as neutral H radical; retain C2H5+. | [NIST ethane appearance energies](https://webbook.nist.gov/cgi/cbook.cgi?ID=C74840&Mask=20), tabulated C2H5+ + H channel. Not generalized to arbitrary C-H bonds. |
| `hydrogen_molecule_loss` | **Ethane motif only:** remove one H from each carbon, increase the C-C bond to double, produce C2H4 radical cation plus H2. | [NIST ethane appearance energies](https://webbook.nist.gov/cgi/cbook.cgi?ID=C74840&Mask=20), tabulated C2H4+ + H2 channel. Atom-level pathway remains a hypothesis. |
| `alcohol_dehydration` | Terminal neutral OH on saturated carbon and an adjacent carbon bearing H; remove O-H plus that beta H as water and create the alpha-beta double bond. | [OpenStax alcohol section](https://openstax.org/books/organic-chemistry/pages/12-3-mass-spectrometry-of-some-common-functional-groups). Only the local beta-H pathway is implemented; other EI dehydrations exist. |
| `mclafferty` | C=O-C(alpha)-C(beta)-C(gamma)-H chain with three single C-C bonds. Alpha-beta cleavage must disconnect; gamma H transfers to O with the corresponding three bond changes. | [OpenStax carbonyl section](https://openstax.org/books/organic-chemistry/pages/12-3-mass-spectrometry-of-some-common-functional-groups). Six-center geometry/energy is not calculated. |
| `acylium_co_loss` | Second step after `carbonyl_alpha`: terminal O and H-free carbonyl carbon leave as CO; one attached carbon fragment retains +1 charge. Both neutral losses are accounted against the original parent. | [CFM-EI paper](https://doi.org/10.1021/acs.analchem.6b01622), multistep fragmentation background; this is a deterministic qualitative subset, not the published learned transition model. |

Every emitted path checks nonnegative integral composition, original atom and
bond references, total element/H accounting, total +1 product charge, and
electron parity for radical versus even-electron products. Computed masses use
specified isotopes or the most abundant isotope and subtract the electron mass
for +1 ions. Isotope peaks are separate annotations and do not earn additional
cleavage credit.

## Added nitrile templates (2026-11-11)

- `vinyl_nitrile_hcn_loss`: terminal neutral `N#C-C(alpha)=C(beta)-H`, nonaromatic neutral carbon endpoints, and an acyclic alpha-beta bond. Remove the named cyano C/N, transfer one beta H to the neutral product, and change alpha-beta from double to triple. The retained radical cation and neutral CHN are checked against the original isotope-aware element/H inventory. No beta H, saturated nitrile, cyclic alkene, isocyanide, or unrelated N-containing group qualifies. Only one primary loss is enumerated, not a recursive HCN-loss chain.
- `aryl_nitrile_cn_loss`: terminal neutral cyano group joined by a single bridge to an aromatic carbon. Remove those two named atoms as a neutral CN radical and retain the complementary even-electron cation. No H is transferred; aliphatic nitriles do not qualify.

Sources: [acrylonitrile HCN elimination study](https://www.sciencedirect.com/science/article/abs/pii/S0009261407015680) and [benzonitrile dissociative ionization study](https://doi.org/10.1039/D3FD00015J). These motivate local templates. Their extension to substituted analogs, beta-H atom mapping, and retained ion connectivity are hypotheses, not experimentally confirmed mechanisms for each candidate. Each path records `reference`, `evidence_basis`, `atom_mapping_status`, and this limitation. `verified` still means template and bookkeeping checks only; no intensity is predicted.

Unsupported: arbitrary HCN chains, unspecified ring double cleavage, arbitrary
hydrogen shifts, unrestricted formula subtractions, general organometallic
fragmentation, and quantitative peak intensity. Extra formula matches are never
promoted to verified paths merely because their m/z agrees with a peak.


## TMS direct-cleavage templates (2026-11-16)

Implementation: [silicon.py](../../src/gcms_agent/fragmentation/silicon.py). Both rules require a neutral,
closed-shell, four-coordinate Si with four single heavy-atom bonds, no Si-H,
and at least three terminal CH3 carbons (implicit H counts). The remaining
attachment must be neutral, nonaromatic, single-bonded C/N/O/S of ordinary
valence 4/3/2/2. Carbonyl-bound, aromatic and unsaturated attachment atoms are
excluded in this first release. Explicit-H methyl groups are not recognized;
13C and silicon isotope labels are retained and their masses are computed.

| Rule | Cut and products | Evidence boundary |
|---|---|---|
| `tms_methyl_radical_loss` | Cut an actual Si-CH3 bridge; retain the connected Si-containing even-electron cation, plus the mapped methyl radical. | No generic mass-minus-15 rule; methyl must belong to the identified TMS motif. |
| `tms_cation_cleavage` | Cut the fourth Si-X bridge only when the Si-side component is exactly Si plus three terminal methyls; retain C3H9Si+ and the complementary radical. | No generic m/z 73 assignment to all silicon compounds. |

Both paths validate explicit ion and neutral product graphs, original atom IDs,
all bond changes, H/element/isotope accounting, total +1 charge and electron
parity using [validate_fragment_products](../../src/gcms_agent/fragmentation/ion_validation.py), before entering the existing formal
path emitter. No H is transferred. Positive charge is represented at the
three-coordinate Si; this is a valid localized representation, not a claim
that the experimental ion has that unique charge localization. Product
validation alone does not establish a mechanism or authorize later reactions.

Primary source: Wang et al., Analytical Chemistry 94 (2022), 1559-1566,
https://doi.org/10.1021/acs.analchem.1c02838 (author manuscript:
https://escholarship.org/uc/item/44c773mt). The O-TMS examples motivate Si-methyl
loss and Si-O cleavage. Applying these direct local templates to N/S/saturated-C
attachments is explicitly a chemical motif extension, not molecule-specific
experimental validation. Every emitted path carries that limitation and its
reference. A reference spectrum alone does not establish the atom mapping
or the cleavage mechanism.

Deferred: non-TMS silicon, Si-H/Si-halogen/Si-Si cleavage; unrestricted Si-C
cleavage; 59/75/147 rearrangements; sequential losses and silicon migrations.
The literature observes such ions in particular classes, but a peak formula
alone does not specify H donors, acceptors, intermediate connectivity, or all
neutral products. No extra mass match is added for these deferred channels.

Improved auditable coverage does not guarantee improved identification:
different isomers can have the same supported-peak set under these direct
templates. The [silicon-rule tests](../../tests/unit/test_silicon_rules.py)
include an explicit isomer pair with shared 73/102/117 support. Preserve the
scoring formula and tie policy when comparing; a matching support set does
not distinguish the structures.
