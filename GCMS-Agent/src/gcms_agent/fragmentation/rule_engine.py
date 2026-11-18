"""Finite EI templates. Applicability is structural; no intensity probability is implied."""
import json
import hashlib
from collections import Counter
from pathlib import Path
from .conservation import atom_counts, formula, masses, check_conservation, electron_parity_ok
from .silicon import SILICON_RULE_IDS, silicon_proposals

RULE_VERSION = "ei-conservative-1.3"
DEFAULT_RULE_PATH = Path(__file__).resolve().parents[3] / "resources" / "ei_rules" / "rules.json"
RULE_IDS = {"molecular_ion", "alkyl_sigma", "carbonyl_alpha", "heteroatom_alpha", "benzylic", "allylic",
            "halogen_radical_loss", "hydrogen_radical_loss", "hydrogen_molecule_loss", "alcohol_dehydration",
            "mclafferty", "acylium_co_loss", "vinyl_nitrile_hcn_loss", "aryl_nitrile_cn_loss"} | SILICON_RULE_IDS


def load_rule_policy(path=None):
    # The checked-in policy is authoritative for both workflow and tool calls.
    # Do not fall back to compiled defaults: that would silently ignore ablations.
    path = DEFAULT_RULE_PATH if path is None else Path(path)
    if path.is_dir():
        path = path / "rules.json"
    raw = path.read_bytes()
    policy = json.loads(raw.decode("utf-8"))
    if not isinstance(policy, dict) or not isinstance(policy.get("version"), str) or not policy["version"].strip():
        raise ValueError("Rule configuration requires a nonempty version")
    enabled = policy.get("enabled")
    if not isinstance(enabled, list) or any(not isinstance(rule, str) for rule in enabled):
        raise ValueError("enabled must be a list of rule IDs")
    unknown = set(enabled) - RULE_IDS
    if unknown or type(policy.get("max_steps")) is not int or policy["max_steps"] not in (1, 2):
        raise ValueError(f"Unsupported rule configuration: {sorted(unknown)}")
    if type(policy.get("max_paths", 10000)) is not int or policy.get("max_paths", 10000) <= 0:
        raise ValueError("max_paths must be a positive integer")
    policy["policy_sha256"] = hashlib.sha256(raw).hexdigest()
    return policy


def enumerate_paths(graph, policy):
    atoms = {a["id"]: a for a in graph["atoms"]}
    bonds = graph["bonds"]
    adjacency = {k: {} for k in atoms}
    for b in bonds:
        adjacency[b["a"]][b["b"]] = float(b["order"])
        adjacency[b["b"]][b["a"]] = float(b["order"])
    enabled = set(policy["enabled"])
    parent = atom_counts(atoms.values())
    all_ids = set(atoms)
    paths = []

    def component(start, removed):
        visited, stack = set(), [start]
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            stack.extend(n for n in adjacency[node] if frozenset((node, n)) not in removed and n not in visited)
        return visited

    def counts(ids):
        return atom_counts(atoms[x] for x in ids)

    def neutral_donor(atom_id):
        atom = atoms[atom_id]
        allowed_valence = {"O": 2, "N": 3, "S": 2}.get(atom["element"])
        return (allowed_valence is not None and atom.get("charge", 0) == 0
                and not atom.get("aromatic")
                and sum(adjacency[atom_id].values()) + atom["hydrogens"] <= allowed_valence)

    def emit(rule, retained, broken=(), h_changes=None, neutral_specs=None, radicals=0, steps=1, bond_changes=None):
        if rule not in enabled or steps > policy["max_steps"]:
            return
        retained = set(retained)
        h_changes = h_changes or {}
        if not retained or not retained <= all_ids or any(x not in atoms for x in h_changes):
            return
        if sum(atoms[x].get("charge", 0) for x in retained) != 0:
            return  # These templates add one charge, not an unspecified charge-transfer path.
        if any(sum(atoms[x].get("charge", 0) for x in spec.get("atom_ids", [])) != 0 for spec in neutral_specs or []):
            return
        if any(atoms[x]["hydrogens"] + delta < 0 for x, delta in h_changes.items()):
            return
        for a, b in broken:
            if b not in adjacency.get(a, {}):
                return
        ion = counts(retained)
        ion[("H", 0)] += sum(delta for x, delta in h_changes.items() if x in retained)
        neutrals = [Counter(spec["counts"]) for spec in (neutral_specs or [])]
        if not check_conservation(parent, ion, neutrals) or not electron_parity_ok(ion, 1, radicals):
            return
        if any(not electron_parity_ok(c, 0, spec.get("radicals", 0)) for c, spec in zip(neutrals, neutral_specs or [])):
            return
        if len(paths) >= policy.get("max_paths", 10000):
            raise ValueError("evidence_enumeration_limit: path budget exceeded; partial coverage must not be ranked")
        paths.append({"rule_id": rule, "atom_ids": sorted(retained), "broken_bonds": [list(p) for p in broken],
                      "hydrogen_changes": dict(h_changes), "bond_changes": bond_changes or [],
                      "ion_formula": formula(ion), "charge": 1, "radicals": radicals,
                      **masses(ion), "neutral_losses": [{"formula": formula(c), "atom_ids": spec.get("atom_ids", []),
                                                        "charge": 0, "radicals": spec.get("radicals", 0)}
                                                       for c, spec in zip(neutrals, neutral_specs or [])],
                      "steps": steps, "verified": True, "verification": {"atom_accounting": True, "charge": True,
                        "electron_parity": True, "template_applicability": True},
                      "verification_scope": "template_and_stoichiometry_only", "mechanism_confirmed": False,
                      "limitations": ["Rule-supported ion hypothesis; formation rate and intensity are not predicted."]})

    emit("molecular_ion", all_ids, radicals=1)
    carbonyls = {c: o for c in atoms if atoms[c]["element"] == "C" for o, order in adjacency[c].items()
                 if atoms[o]["element"] == "O" and order == 2}
    all_carbon = all(a["element"] == "C" for a in atoms.values())
    for bond in sorted(bonds, key=lambda b: tuple(sorted((b["a"], b["b"])))):
        a, b = bond["a"], bond["b"]
        if float(bond["order"]) != 1:
            continue
        for keep, leave in ((a, b), (b, a)):
            if atoms[keep]["element"] != "C" or atoms[keep].get("aromatic") or atoms[keep].get("charge", 0):
                continue
            retained = component(keep, {frozenset((a, b))})
            if leave in retained:
                continue  # No generic ring double-cut guessing.
            other = all_ids - retained
            neutral = {"counts": counts(other), "atom_ids": sorted(other), "radicals": 1}
            rule = None
            neighbors = [(n, order) for n, order in adjacency[keep].items() if n != leave]
            if keep in carbonyls and atoms[leave]["element"] == "C":
                rule = "carbonyl_alpha"
            elif atoms[leave]["element"] == "C" and any(order == 1 and neutral_donor(n) for n, order in neighbors):
                rule = "heteroatom_alpha"
            elif any(atoms[n].get("aromatic") and order == 1 for n, order in neighbors):
                rule = "benzylic"
            # A classical allylic site is saturated in the parent and next
            # to C=C. A vinylic/sp carbon near another alkene is not sufficient.
            # Inspect every bond at keep, not just the remaining neighbors.
            elif (all(order == 1 for order in adjacency[keep].values())
                  and any(atoms[n]["element"] == "C" and order == 1
                          and any(v == 2 and atoms[t]["element"] == "C"
                                  for t, v in adjacency[n].items())
                          for n, order in neighbors)):
                rule = "allylic"
            elif all_carbon and all(order == 1 for order in adjacency[keep].values()):
                rule = "alkyl_sigma"
            elif atoms[leave]["element"] in ("F", "Cl", "Br", "I") and len(other) == 1 and all(order == 1 for order in adjacency[keep].values()):
                rule = "halogen_radical_loss"
            if rule:
                emit(rule, retained, ((a, b),), neutral_specs=[neutral])
            if rule == "carbonyl_alpha":
                oxygen = carbonyls[keep]
                alkyl = retained - {keep, oxygen}
                r = [n for n in adjacency[keep] if n in alkyl]
                if len(r) == 1 and atoms[r[0]]["element"] == "C" and len(adjacency[oxygen]) == 1 and atoms[keep]["hydrogens"] == 0:
                    # RCO+ -> R+ + CO, following parent alpha cleavage.
                    co = counts({keep, oxygen})
                    emit("acylium_co_loss", alkyl, ((a, b), (keep, r[0])), neutral_specs=[neutral,
                         {"counts": co, "atom_ids": sorted((keep, oxygen)), "radicals": 0}], steps=2)

    for carbon, atom in sorted(atoms.items()):
        if atom["element"] != "C" or atom["hydrogens"] < 1 or atom.get("aromatic"):
            continue
        # The first release deliberately limits M-H/M-H2 to ethane's experimentally
        # tabulated channels. Applying these losses at every saturated carbon would
        # turn the scorer into a permissive formula-matching search.
        ethane = (all_carbon and len(atoms) == 2 and len(bonds) == 1
                  and float(bonds[0]["order"]) == 1
                  and all(a["hydrogens"] == 3 and not a.get("aromatic") for a in atoms.values()))
        if ethane:
            emit("hydrogen_radical_loss", all_ids, h_changes={carbon: -1}, neutral_specs=[
                {"counts": Counter({("H", 0): 1}), "radicals": 1}])
        if ethane:
            for neighbor, order in adjacency[carbon].items():
                if carbon < neighbor and order == 1 and atoms[neighbor]["hydrogens"] >= 1 and not atoms[neighbor].get("aromatic"):
                    emit("hydrogen_molecule_loss", all_ids, h_changes={carbon: -1, neighbor: -1},
                         neutral_specs=[{"counts": Counter({("H", 0): 2}), "radicals": 0}], radicals=1,
                         bond_changes=[{"a": carbon, "b": neighbor, "from": 1, "to": 2}])

    for oxygen, atom in sorted(atoms.items()):
        if atom["element"] != "O" or atom["hydrogens"] != 1 or len(adjacency[oxygen]) != 1:
            continue
        alpha = next(iter(adjacency[oxygen]))
        if atoms[alpha]["element"] != "C" or any(order != 1 for order in adjacency[alpha].values()):
            continue
        for beta, order in adjacency[alpha].items():
            if beta == oxygen or atoms[beta]["element"] != "C" or order != 1 or atoms[beta]["hydrogens"] < 1:
                continue
            water = counts({oxygen}); water[("H", 0)] += 1
            emit("alcohol_dehydration", all_ids - {oxygen}, ((oxygen, alpha),), {beta: -1},
                 [{"counts": water, "atom_ids": [oxygen], "radicals": 0}], radicals=1,
                 bond_changes=[{"a": alpha, "b": beta, "from": 1, "to": 2}])

    for carbonyl, oxygen in sorted(carbonyls.items()):
        for alpha, order1 in adjacency[carbonyl].items():
            if atoms[alpha]["element"] != "C" or order1 != 1:
                continue
            for beta, order2 in adjacency[alpha].items():
                if beta in (carbonyl, oxygen) or atoms[beta]["element"] != "C" or order2 != 1:
                    continue
                for gamma, order3 in adjacency[beta].items():
                    if gamma in (carbonyl, oxygen, alpha) or atoms[gamma]["element"] != "C" or order3 != 1 or atoms[gamma]["hydrogens"] < 1:
                        continue
                    retained = component(carbonyl, {frozenset((alpha, beta))})
                    if beta in retained or gamma in retained:
                        continue
                    other = all_ids - retained
                    neutral = counts(other); neutral[("H", 0)] -= 1
                    emit("mclafferty", retained, ((alpha, beta),), {oxygen: 1, gamma: -1},
                         [{"counts": neutral, "atom_ids": sorted(other), "radicals": 0}], radicals=1,
                         bond_changes=[{"a": carbonyl, "b": oxygen, "from": 2, "to": 1},
                                       {"a": carbonyl, "b": alpha, "from": 1, "to": 2},
                                       {"a": beta, "b": gamma, "from": 1, "to": 2}])
    # Narrow, literature-motivated nitrile templates. These encode proposed
    # atom mappings, not experimentally established mechanisms for every analog.
    for nitrogen, atom in sorted(atoms.items()):
        if (atom["element"] != "N" or atom.get("charge", 0) or atom["hydrogens"]
                or atom.get("aromatic") or len(adjacency[nitrogen]) != 1):
            continue
        cyano = next(iter(adjacency[nitrogen]))
        if (adjacency[nitrogen][cyano] != 3 or atoms[cyano]["element"] != "C"
                or atoms[cyano]["hydrogens"] or atoms[cyano].get("charge", 0)
                or len(adjacency[cyano]) != 2):
            continue
        alpha = next(n for n in adjacency[cyano] if n != nitrogen)
        if atoms[alpha]["element"] != "C" or adjacency[cyano][alpha] != 1:
            continue
        retained = component(alpha, {frozenset((alpha, cyano))})
        if all_ids - retained != {cyano, nitrogen}:
            continue
        cn = counts({cyano, nitrogen})
        before = len(paths)
        if atoms[alpha].get("aromatic"):
            emit("aryl_nitrile_cn_loss", retained, ((alpha, cyano),),
                 neutral_specs=[{"counts": cn, "atom_ids": sorted((cyano, nitrogen)), "radicals": 1}])
        else:
            # N#C-C(alpha)=C(beta)-H -> HCN + C(alpha)#C(beta).
            # Both alkene atoms must be nonaromatic, neutral carbons. Beta-H
            # is explicitly removed and the retained double bond becomes triple.
            for beta, order in sorted(adjacency[alpha].items()):
                if (order != 2 or atoms[beta]["element"] != "C" or atoms[beta].get("aromatic")
                        or atoms[alpha].get("charge", 0) or atoms[beta].get("charge", 0)
                        or atoms[beta]["hydrogens"] < 1):
                    continue
                # No ring alkyne construction or unspecified extra bond changes.
                if beta in component(alpha, {frozenset((alpha, beta))}):
                    continue
                hcn = cn.copy()
                hcn[("H", 0)] += 1
                emit("vinyl_nitrile_hcn_loss", retained, ((alpha, cyano),), {beta: -1},
                     [{"counts": hcn, "atom_ids": sorted((cyano, nitrogen)), "radicals": 0}],
                     radicals=1, bond_changes=[{"a": alpha, "b": beta, "from": 2, "to": 3}])
        for path in paths[before:]:
            path["evidence_basis"] = "literature_motivated_local_template"
            path["atom_mapping_status"] = "hypothesis_not_experimentally_confirmed"
            path["reference"] = ("https://www.sciencedirect.com/science/article/abs/pii/S0009261407015680"
                if path["rule_id"] == "vinyl_nitrile_hcn_loss" else "https://doi.org/10.1039/D3FD00015J")
            path["limitations"].append("Local-motif extension from a studied parent; substitution effects and atom mapping are not experimentally validated here.")
    if enabled & SILICON_RULE_IDS and any(a["element"] == "Si" for a in atoms.values()):
        for proposal in silicon_proposals(graph):
            metadata = proposal.pop("metadata")
            before = len(paths)
            emit(**proposal)
            for path in paths[before:]:
                path.update(metadata)
                path["verification"]["product_representation"] = True
                path["limitations"].append(metadata["scope_note"])
    # Stable ordering and exact mechanism deduplication, not path-count weighting.
    unique = {json.dumps(p, sort_keys=True): p for p in paths}
    return [unique[k] for k in sorted(unique)]

