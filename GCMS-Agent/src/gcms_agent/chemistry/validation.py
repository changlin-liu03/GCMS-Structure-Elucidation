"""Strict chemical gate for every non-null final SMILES."""
from __future__ import annotations

from copy import deepcopy

from rdkit import Chem, rdBase

from .graph_checks import check_graph, composition
from .molecode_adapter import parse_graph, render_graph
from .properties import molecular_properties
from .smiles_io import mol_from_graph


def validate_graph(graph: dict, expected_composition: dict | None = None,
                   require_connected: bool = True, require_neutral: bool = True,
                   require_closed_shell: bool = True) -> dict:
    result = {"valid": False, "errors": [], "canonical_smiles": None,
              "formula": None, "exact_mass": None, "nominal_mass": None,
              "graph": deepcopy(graph), "molecode": None, "composition": {}}
    errors = check_graph(graph)
    if errors:
        result["errors"] = errors
        return result
    result["composition"] = composition(graph)
    if expected_composition is not None and result["composition"] != dict(expected_composition):
        result["errors"] = ["Heavy-atom composition changed"]
        return result
    try:
        result["molecode"] = render_graph(graph)
        table = Chem.GetPeriodicTable()
        for atom in graph["atoms"]:
            isotope = atom.get("isotope", 0)
            if isotope and table.GetMassForIsotope(table.GetAtomicNumber(atom["element"]), isotope) <= 0:
                errors.append(f"Unsupported isotope at {atom['id']}: {isotope}{atom['element']} has no known RDKit isotope mass")
        if errors:
            result["errors"] = errors
            return result
        with rdBase.BlockLogs():
            mol = mol_from_graph(graph, sanitize=True)
        if require_connected and len(Chem.GetMolFrags(mol)) != 1:
            errors.append("Final parent must be a single connected molecule")
        if require_neutral and Chem.GetFormalCharge(mol) != 0:
            errors.append("Final parent must have total formal charge zero")
        if require_closed_shell and any(a.GetNumRadicalElectrons() for a in mol.GetAtoms()):
            errors.append("Final parent must be closed-shell; fragment radicals use a separate representation")
        for original, atom in zip(graph["atoms"], mol.GetAtoms()):
            if atom.GetTotalNumHs() != original.get("hydrogens"):
                errors.append(f"Hydrogen state changed during sanitization: {original['id']}")
            if atom.GetNumRadicalElectrons() != original.get("radicals", 0):
                errors.append(f"Radical state changed during sanitization: {original['id']}")
            if int(atom.GetChiralTag()) != original.get("chiral_tag", 0):
                errors.append(f"Chiral state changed during sanitization: {original['id']}")
        canonical = Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)
        with rdBase.BlockLogs():
            back = Chem.MolFromSmiles(canonical)
        if back is None or Chem.MolToSmiles(back, canonical=True, isomericSmiles=True) != canonical:
            errors.append("Strict SMILES round-trip failed")
        decoded = parse_graph(result["molecode"])
        def core_attributes(g):
            atoms = [(a["id"], a["element"], a.get("isotope", 0), a.get("charge", 0),
                      a.get("hydrogens"), a.get("radicals", 0), a.get("aromatic", False),
                      a.get("chiral_tag", 0), a.get("atom_map", 0)) for a in g["atoms"]]
            bonds = [(b["a"], b["b"], b["order"], b.get("stereo", 0),
                      tuple(b.get("stereo_atoms", []))) for b in g["bonds"]]
            return atoms, bonds

        if core_attributes(decoded) != core_attributes(graph):
            errors.append("MoleCode round-trip changed graph attributes")
        roundtrip = mol_from_graph(decoded, sanitize=True)
        if Chem.MolToSmiles(roundtrip, canonical=True, isomericSmiles=True) != canonical:
            errors.append("MoleCode round-trip changed molecular identity")
        if back is not None:
            props = molecular_properties(mol)
            back_props = molecular_properties(back)
            if any(props[key] != back_props[key] for key in ("formula", "nominal_mass", "hydrogen_count", "formal_charge")):
                errors.append("SMILES round-trip changed molecular composition")
            result.update(props)
        if not errors:
            result.update(valid=True, canonical_smiles=canonical)
    except Exception as exc:
        errors.append(f"Strict validation failed: {exc}")
    result["errors"] = errors
    return result

