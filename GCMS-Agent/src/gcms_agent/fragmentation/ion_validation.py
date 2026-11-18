"""Validate explicit product representations, not EI mechanisms or abundances.

Product graphs retain parent atom IDs. Implicit H counts can move between
mapped atoms; isotope-labelled H must be an explicit mapped atom. A reaction
that creates explicit H from an implicit count must first expand the parent H.
"""
from collections import Counter
from copy import deepcopy

from gcms_agent.chemistry.smiles_io import mol_from_graph
from gcms_agent.chemistry.validation import validate_graph
from .conservation import atom_counts, check_conservation, electron_parity_ok


def validate_fragment_ion(graph, expected_charge=1):
    """Allow radicals/charge while enforcing strict graph and round-trip checks."""
    result = validate_graph(graph, require_neutral=False, require_closed_shell=False)
    if type(expected_charge) is not int or expected_charge == 0:
        result["errors"].append("Expected fragment charge must be a nonzero integer")
    if result["valid"]:
        # RDKit permits some hypercoordinate charged representations (e.g.
        # CH5+). These ordinary bond graphs do not model multicentre bonding;
        # conservatively exclude it from this product representation contract.
        mol = mol_from_graph(graph)
        for atom, item in zip(mol.GetAtoms(), graph["atoms"]):
            cap = {"H": 1, "B": 4, "C": 4, "N": 4, "O": 3, "F": 1}.get(atom.GetSymbol())
            if cap is not None and atom.GetTotalValence() > cap:
                result["errors"].append(f"Unsupported product valence at {item['id']}")
        charge = sum(a.get("charge", 0) for a in graph["atoms"])
        radicals = sum(a.get("radicals", 0) for a in graph["atoms"])
        if charge != expected_charge:
            result["errors"].append("Fragment total charge differs from expected charge")
        if not electron_parity_ok(atom_counts(graph["atoms"]), charge, radicals):
            result["errors"].append("Fragment electron/radical parity is inconsistent")
        result["radicals"] = radicals
    result["valid"] = not result["errors"]
    result.update(representation_valid=result["valid"], mechanism_confirmed=False,
                  used_for_scoring=False, verification_scope="ion_representation_only")
    return result


def _bond_inventory(graph):
    # Compare perceived aromatic bonds, independent of the Kekule form supplied.
    mol = mol_from_graph(graph)
    ids = [a["id"] for a in graph["atoms"]]
    return {tuple(sorted((ids[b.GetBeginAtomIdx()], ids[b.GetEndAtomIdx()]))):
            1.5 if b.GetIsAromatic() else b.GetBondTypeAsDouble()
            for b in mol.GetBonds()}


def validate_fragment_products(parent_graph, ion_graph, neutral_graphs, *,
                               hydrogen_changes, bond_changes):
    """Audit a proposed single ionization (+1) with explicitly mapped products.

hydrogen_changes declares net implicit H deltas per stable atom ID (not a
proven trajectory). bond_changes declares ALL breaks, formations and order
changes with {a,b,from,to}, using 0 for absent and 1.5 for aromatic bonds.
Passing this audit never registers a rule or licenses a sequential reaction.
"""
    parent = validate_graph(parent_graph)
    ion = validate_fragment_ion(ion_graph)
    neutrals = [validate_graph(g, require_closed_shell=False) for g in neutral_graphs]
    result = {"valid": False, "errors": [], "ion_validation": ion,
              "neutral_validations": neutrals, "product_graph": deepcopy(ion_graph),
              "neutral_products": deepcopy(neutral_graphs), "atom_mapping": None,
              "hydrogen_changes": deepcopy(hydrogen_changes), "bond_changes": deepcopy(bond_changes),
              "mechanism_confirmed": False, "used_for_scoring": False,
              "can_seed_next_step": False, "verification_scope": "product_representation_and_accounting_only"}
    for label, validation in [("parent", parent), ("ion", ion),
                              *[(f"neutral[{i}]", v) for i, v in enumerate(neutrals)]]:
        result["errors"].extend(f"{label}: {error}" for error in validation["errors"])
    if result["errors"]:
        return result
    original = {a["id"]: a for a in parent_graph["atoms"]}
    products = [ion_graph, *neutral_graphs]
    all_atoms = [a for g in products for a in g["atoms"]]
    if Counter(a["id"] for a in all_atoms) != Counter({i: 1 for i in original}):
        result["errors"].append("Products must partition parent atom IDs exactly once")
        return result
    result["atom_mapping"] = {a["id"]: a["id"] for a in all_atoms}
    for atom in all_atoms:
        before = original[atom["id"]]
        if (atom["element"], atom.get("isotope", 0)) != (before["element"], before.get("isotope", 0)):
            result["errors"].append(f"Element/isotope changed at {atom['id']}")
    if not check_conservation(atom_counts(parent_graph["atoms"]), atom_counts(ion_graph["atoms"]),
                              [atom_counts(g["atoms"]) for g in neutral_graphs]):
        result["errors"].append("Product element/isotope/hydrogen accounting is not conserved")
    actual_h = {a["id"]: a["hydrogens"] - original[a["id"]]["hydrogens"] for a in all_atoms
                if a["hydrogens"] != original[a["id"]]["hydrogens"]}
    if (not isinstance(hydrogen_changes, dict)
            or any(type(v) is not int or v == 0 for v in hydrogen_changes.values())
            or hydrogen_changes != actual_h):
        result["errors"].append("Declared hydrogen changes differ from product graphs")
    old_bonds = _bond_inventory(parent_graph)
    new_bonds = {k: v for g in products for k, v in _bond_inventory(g).items()}
    actual_bonds = {k: (old_bonds.get(k, 0), new_bonds.get(k, 0))
                    for k in old_bonds.keys() | new_bonds.keys()
                    if old_bonds.get(k, 0) != new_bonds.get(k, 0)}
    declared = {}
    try:
        if not isinstance(bond_changes, list):
            raise ValueError()
        for b in bond_changes:
            key = tuple(sorted((b["a"], b["b"])))
            if key in declared or key[0] == key[1] or not set(key) <= original.keys():
                raise ValueError()
            if any(type(b[x]) not in (int, float) or b[x] not in (0, 1, 1.5, 2, 3) for x in ("from", "to")):
                raise ValueError()
            declared[key] = (b["from"], b["to"])
        if declared != actual_bonds:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        result["errors"].append("Declared bond changes differ from product graphs")
    for g in neutral_graphs:
        if not electron_parity_ok(atom_counts(g["atoms"]), 0, sum(a.get("radicals", 0) for a in g["atoms"])):
            result["errors"].append("Neutral electron/radical parity is inconsistent")
    result["valid"] = not result["errors"]
    return result
