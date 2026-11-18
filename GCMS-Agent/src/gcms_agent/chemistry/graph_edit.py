"""Atomic graph transactions with no chemical edit-count limit."""
from __future__ import annotations

from copy import deepcopy

from .graph_checks import require_graph


def apply_patch(graph: dict, operations: list[dict]) -> dict:
    require_graph(graph)
    if not isinstance(operations, list):
        raise ValueError("operations must be a list")
    edited = deepcopy(graph)
    atoms = {a["id"]: a for a in edited["atoms"]}
    for operation in operations:
        if not isinstance(operation, dict):
            raise ValueError("Each operation must be an object")
        kind = operation.get("op")
        if kind in ("set_bond", "remove_bond"):
            allowed = {"op", "a", "b", "order", "stereo", "stereo_atoms"} if kind == "set_bond" else {"op", "a", "b"}
            if set(operation) - allowed:
                raise ValueError("Unknown bond operation fields")
            a, b = operation.get("a"), operation.get("b")
            if a not in atoms or b not in atoms or a == b:
                raise ValueError("Bond operation must reference two existing distinct atoms")
            existing = next((x for x in edited["bonds"] if {x["a"], x["b"]} == {a, b}), None)
            if kind == "remove_bond":
                if existing is None:
                    raise ValueError(f"Cannot remove nonexistent bond {a}-{b}")
                edited["bonds"].remove(existing)
            else:
                order = operation.get("order")
                if type(order) not in (int, float) or order not in (1, 1.5, 2, 3):
                    raise ValueError("Unsupported or missing bond order")
                stereo = operation.get("stereo", existing.get("stereo", 0) if existing and existing["order"] == order else 0)
                refs = operation.get("stereo_atoms", existing.get("stereo_atoms", []) if existing and existing["order"] == order else [])
                replacement = {"a": a, "b": b, "order": order, "stereo": stereo, "stereo_atoms": refs}
                if existing:
                    # Keep original direction when carrying existing stereo refs.
                    if "stereo_atoms" not in operation and existing["a"] != a and refs:
                        replacement["stereo_atoms"] = list(reversed(refs))
                    edited["bonds"][edited["bonds"].index(existing)] = replacement
                else:
                    edited["bonds"].append(replacement)
        elif kind == "set_atom":
            if set(operation) - {"op", "id", "hydrogens", "charge", "radicals", "aromatic"}:
                raise ValueError("Atom edits can only set hydrogens, charge, radicals, or aromatic flag")
            atom_id = operation.get("id")
            if atom_id not in atoms:
                raise ValueError("Unknown atom ID")
            updates = {key: value for key, value in operation.items() if key not in ("op", "id")}
            if not updates:
                raise ValueError("set_atom has no changes")
            atoms[atom_id].update(updates)
        else:
            raise ValueError(f"Unsupported graph operation: {kind}")
    # Aromatic membership follows final explicit aromatic bonds, never a hidden
    # kekulization repair. H counts are NEVER filled in by an edit operation.
    aromatic_ids = {endpoint for b in edited["bonds"] if b["order"] == 1.5 for endpoint in (b["a"], b["b"])}
    for atom in edited["atoms"]:
        if atom["id"] in aromatic_ids:
            atom["aromatic"] = True
        elif any(o.get("op") in ("set_bond", "remove_bond") and atom["id"] in (o.get("a"), o.get("b")) for o in operations):
            atom["aromatic"] = False
    # RDKit tetrahedral tags are relative to neighbour insertion order. Removing
    # and re-adding the same edge must not silently invert an untouched center.
    def neighbors(g, atom_id):
        return [b["b"] if b["a"] == atom_id else b["a"] for b in g["bonds"]
                if atom_id in (b["a"], b["b"])]

    for original in graph["atoms"]:
        if not original.get("chiral_tag", 0):
            continue
        atom = atoms[original["id"]]
        before = neighbors(graph, original["id"])
        after = neighbors(edited, original["id"])
        if set(before) != set(after) or atom.get("hydrogens") != original.get("hydrogens"):
            raise ValueError("Editing the connectivity of a specified stereocenter requires an explicit stereo reassignment, unsupported in this version")
        permutation = [before.index(a) for a in after]
        inversions = sum(permutation[i] > permutation[j] for i in range(len(permutation))
                         for j in range(i + 1, len(permutation)))
        if inversions % 2:
            atom["chiral_tag"] = 3 - atom["chiral_tag"]
    require_graph(edited)
    return edited

