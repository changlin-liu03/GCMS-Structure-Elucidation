"""Conservative one-bond TMS hypotheses, with explicit product validation.

These local templates do not establish mechanism, abundance, or ion uniqueness.
Hydride, halo, Si-Si, non-TMS silicon and rearrangements are outside this release.
"""
from copy import deepcopy

from .conservation import atom_counts
from .ion_validation import validate_fragment_products

SILICON_RULE_IDS = {"tms_methyl_radical_loss", "tms_cation_cleavage"}
REFERENCE = "https://doi.org/10.1021/acs.analchem.1c02838"


def silicon_proposals(graph):
    atoms = {a["id"]: a for a in graph["atoms"]}
    adjacency = {i: {} for i in atoms}
    for b in graph["bonds"]:
        adjacency[b["a"]][b["b"]] = b["order"]
        adjacency[b["b"]][b["a"]] = b["order"]
    if any(a.get("charge", 0) or a.get("radicals", 0) for a in atoms.values()):
        return

    def methyl(i, si):
        a = atoms[i]
        # Explicit isotope-labelled H is excluded, rather than silently lost.
        return (a["element"] == "C" and not a.get("aromatic")
                and a["hydrogens"] == 3 and set(adjacency[i]) == {si}
                and adjacency[i][si] == 1)

    def allowed_attachment(i):
        a = atoms[i]
        cap = {"C": 4, "N": 3, "O": 2, "S": 2}.get(a["element"])
        return (cap is not None and not a.get("aromatic")
                and all(v == 1 for v in adjacency[i].values())
                and sum(adjacency[i].values()) + a["hydrogens"] == cap)

    def component(start, a, b):
        found, todo = set(), [start]
        while todo:
            i = todo.pop()
            if i in found:
                continue
            found.add(i)
            todo.extend(j for j in adjacency[i]
                        if {i, j} != {a, b} and j not in found)
        return found

    def product(ids, charge_at=None, radical_at=None):
        result = deepcopy(graph)
        result["atoms"] = [deepcopy(a) for a in graph["atoms"] if a["id"] in ids]
        result["bonds"] = [deepcopy(b) for b in graph["bonds"]
                           if b["a"] in ids and b["b"] in ids]
        for a in result["atoms"]:
            a["charge"] = int(a["id"] == charge_at)
            a["radicals"] = int(a["id"] == radical_at)
        return result

    for si, atom in sorted(atoms.items()):
        if (atom["element"] != "Si" or atom.get("aromatic") or atom["hydrogens"]
                or len(adjacency[si]) != 4 or any(v != 1 for v in adjacency[si].values())):
            continue
        methyls = {i for i in adjacency[si] if methyl(i, si)}
        if len(methyls) < 3 or not all(allowed_attachment(i) for i in adjacency[si]):
            continue
        for leaving in sorted(adjacency[si]):
            retained = component(si, si, leaving)
            other = set(atoms) - retained
            if leaving not in other or component(leaving, si, leaving) != other:
                continue
            rules = []
            if leaving in methyls:
                rules.append("tms_methyl_radical_loss")
            if retained == {si} | (methyls - {leaving}) and len(retained) == 4:
                rules.append("tms_cation_cleavage")
            if not rules:
                continue
            ion = product(retained, charge_at=si)
            neutral = product(other, radical_at=leaving)
            changes = [{"a": si, "b": leaving, "from": 1, "to": 0}]
            audit = validate_fragment_products(graph, ion, [neutral],
                                               hydrogen_changes={}, bond_changes=changes)
            if not audit["valid"]:
                continue
            # This is an explicitly registered formal template; the validator
            # alone does not authorize a path or a sequential reaction.
            for rule in rules:
                yield {
                    "rule": rule, "retained": retained, "broken": ((si, leaving),),
                    "neutral_specs": [{"counts": atom_counts(neutral["atoms"]),
                                       "atom_ids": sorted(other), "radicals": 1}],
                    "bond_changes": changes,
                    "metadata": {"product_graph": ion, "neutral_products": [neutral],
                                 "product_representation_valid": True,
                                 "atom_mapping": audit["atom_mapping"],
                                 "attachment_element": atoms[leaving]["element"],
                                 "evidence_basis": "literature_motivated_local_template",
                                 "atom_mapping_status": "hypothesis_not_experimentally_confirmed",
                                 "reference": REFERENCE,
                                 "scope_note": "Direct TMS cleavage; N/S/saturated-C environments are local-motif extensions of the documented O-TMS channels. No abundance or unique mechanism claim."}}
