"""Structural checks that deliberately do not require chemical validity."""
from __future__ import annotations

import re
from collections import Counter

from rdkit import Chem

ATOM_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
ORDERS = {1, 1.5, 2, 3}


def composition(graph: dict) -> dict[str, int]:
    return dict(sorted(Counter(a["element"] for a in graph["atoms"]
                               if a["element"] != "H").items()))


def check_graph(graph: dict) -> list[str]:
    errors = []
    if not isinstance(graph, dict) or not isinstance(graph.get("atoms"), list) or not isinstance(graph.get("bonds"), list):
        return ["graph must contain atoms and bonds lists"]
    if not graph["atoms"]:
        errors.append("graph contains no atoms")
    seen = set()
    for atom in graph["atoms"]:
        if not isinstance(atom, dict):
            errors.append("atom must be an object")
            continue
        atom_id = atom.get("id")
        if not isinstance(atom_id, str) or not ATOM_ID.fullmatch(atom_id):
            errors.append(f"invalid atom id: {atom_id!r}")
            continue
        if atom_id in seen:
            errors.append(f"duplicate atom id: {atom_id}")
        seen.add(atom_id)
        # Upstream MoleCode reserves these suffixes for absolute CIP labels.
        # RDKit chiral_tag is a relative ordering tag, not an R/S assignment;
        # accepting a suffix as an ordinary ID would silently erase stereo.
        if atom_id.endswith(("_R", "_S")):
            errors.append(f"unsupported absolute CIP suffix at {atom_id}; use SMILES-derived stereo metadata and stable IDs without _R/_S")
        try:
            element = atom.get("element", "")
            if not isinstance(element, str) or not re.fullmatch(r"[A-Z][a-z]?", element) or Chem.GetPeriodicTable().GetAtomicNumber(element) < 1:
                raise ValueError()
        except Exception:
            errors.append(f"invalid element at {atom_id}")
        for key in ("isotope", "radicals"):
            value = atom.get(key, 0)
            if type(value) is not int or value < 0:
                errors.append(f"{atom_id}.{key} must be a nonnegative integer")
        if type(atom.get("charge", 0)) is not int:
            errors.append(f"{atom_id}.charge must be an integer")
        if type(atom.get("atom_map", 0)) is not int or atom.get("atom_map", 0) < 0:
            errors.append(f"{atom_id}.atom_map must be a nonnegative integer")
        hydrogen = atom.get("hydrogens")
        if hydrogen is not None and (type(hydrogen) is not int or hydrogen < 0):
            errors.append(f"{atom_id}.hydrogens must be a nonnegative integer or null")
        if type(atom.get("aromatic", False)) is not bool:
            errors.append(f"{atom_id}.aromatic must be boolean")
        if type(atom.get("chiral_tag", 0)) is not int or atom.get("chiral_tag", 0) not in (0, 1, 2):
            errors.append(f"unsupported chirality at {atom_id}")
    edges = set()
    for bond in graph["bonds"]:
        if not isinstance(bond, dict):
            errors.append("bond must be an object")
            continue
        a, b = bond.get("a"), bond.get("b")
        if not isinstance(a, str) or not isinstance(b, str):
            errors.append("bond endpoints must be atom IDs")
            continue
        if a not in seen or b not in seen:
            errors.append(f"dangling bond: {a}-{b}")
        if a == b:
            errors.append(f"self bond: {a}")
        edge = tuple(sorted((a, b)))
        if edge in edges:
            errors.append(f"duplicate bond: {a}-{b}")
        edges.add(edge)
        if type(bond.get("order")) not in (int, float) or bond["order"] not in ORDERS:
            errors.append(f"unsupported bond order: {a}-{b}")
        stereo = bond.get("stereo", 0)
        refs = bond.get("stereo_atoms", [])
        if type(stereo) is not int or stereo not in (0, 1, 2, 3, 4, 5):
            errors.append(f"unsupported bond stereo: {a}-{b}")
        if not isinstance(refs, list) or any(not isinstance(r, str) or r not in seen for r in refs):
            errors.append(f"invalid stereo references: {a}-{b}")
        elif stereo not in (0, 1) and (bond.get("order") != 2 or len(refs) != 2):
            errors.append(f"defined stereo requires double bond and two references: {a}-{b}")
        elif stereo == 0 and refs:
            errors.append(f"stereo references without stereo: {a}-{b}")
    return errors


def require_graph(graph: dict) -> None:
    errors = check_graph(graph)
    if errors:
        raise ValueError("; ".join(errors))

