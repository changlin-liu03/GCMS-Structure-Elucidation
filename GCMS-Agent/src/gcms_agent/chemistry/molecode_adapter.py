"""Strict small-molecule MoleCode codec, based on the upstream public grammar.

Versioned %%@ metadata preserves RDKit stereochemical reference atoms, radicals,
and atom maps that the upstream display labels alone cannot encode. Unknown
non-comment syntax is rejected, never silently discarded. Chemical validity is
intentionally deferred to validation.validate_graph.
"""
from __future__ import annotations

import json
import re

from .graph_checks import require_graph

OPERATORS = {1: "---", 2: "===", 3: "-.-", 1.5: "<-->"}
NODE = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)\[([^\[\]]+)\]$")
EDGE = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)\s*(<-->|---|===|-\.-)(?:\|([EZ])\|)?\s*([A-Za-z][A-Za-z0-9_]*)$")
LABEL = re.compile(r"^(\d+)?([A-Z][a-z]?)(?:H(\d*))?(?:\((\d*)([+-])\))?$")


def _label(atom: dict) -> str:
    label = (str(atom["isotope"]) if atom.get("isotope", 0) else "") + atom["element"]
    hydrogen = atom.get("hydrogens")
    if hydrogen:
        label += "H" + (str(hydrogen) if hydrogen != 1 else "")
    charge = atom.get("charge", 0)
    if charge:
        label += "(" + (str(abs(charge)) if abs(charge) != 1 else "") + ("+" if charge > 0 else "-") + ")"
    if hydrogen is None:
        label += ";H=?"
    return label


def render_graph(graph: dict) -> str:
    require_graph(graph)
    lines = ["graph TB", "    %%@codec gcms-molecode-1", '    subgraph Molecule["Candidate"]']
    for atom in graph["atoms"]:
        metadata = {key: atom.get(key, default) for key, default in
                    (("aromatic", False), ("radicals", 0), ("chiral_tag", 0), ("atom_map", 0))}
        lines.append(f"        %%@atom {atom['id']} {json.dumps(metadata, separators=(',', ':'))}")
        lines.append(f"        {atom['id']}[{_label(atom)}]")
    for bond in graph["bonds"]:
        metadata = {"stereo": bond.get("stereo", 0), "stereo_atoms": bond.get("stereo_atoms", [])}
        lines.append(f"        %%@bond {bond['a']} {bond['b']} {json.dumps(metadata, separators=(',', ':'))}")
        stereo_label = {2: "|Z|", 3: "|E|"}.get(bond.get("stereo", 0), "")
        lines.append(f"        {bond['a']} {OPERATORS[bond['order']]}{stereo_label} {bond['b']}")
    lines.append("    end")
    return "\n".join(lines) + "\n"


def parse_graph(text: str) -> dict:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("MoleCode must be nonempty text")
    graph = {"atoms": [], "bonds": []}
    atom_meta, bond_meta = {}, {}
    begun = False
    depth = 0
    bond_labels = {}
    for line_number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("%%@codec "):
            if line != "%%@codec gcms-molecode-1":
                raise ValueError(f"Unknown MoleCode codec on line {line_number}")
            continue
        if line.startswith("%%@atom "):
            parts = line.split(maxsplit=2)
            if len(parts) != 3 or parts[1] in atom_meta:
                raise ValueError(f"Malformed or duplicate atom metadata on line {line_number}")
            metadata = json.loads(parts[2])
            if not isinstance(metadata, dict) or set(metadata) - {"aromatic", "radicals", "chiral_tag", "atom_map"}:
                raise ValueError("Unknown atom metadata fields")
            atom_meta[parts[1]] = metadata
            continue
        if line.startswith("%%@bond "):
            parts = line.split(maxsplit=3)
            if len(parts) != 4:
                raise ValueError(f"Malformed bond metadata on line {line_number}")
            key = (parts[1], parts[2])
            if key in bond_meta:
                raise ValueError("Duplicate bond metadata")
            metadata = json.loads(parts[3])
            if not isinstance(metadata, dict) or set(metadata) - {"stereo", "stereo_atoms"}:
                raise ValueError("Unknown bond metadata fields")
            bond_meta[key] = metadata
            continue
        if line.startswith("%%@"):
            raise ValueError("Unknown reserved metadata directive")
        if line.startswith("%%"):
            continue
        # Normal inline comments are permitted by the upstream MoleCode grammar.
        line = line.split("%%", 1)[0].strip()
        if line in ("graph TB", "graph LR"):
            if begun:
                raise ValueError("Duplicate graph header")
            begun = True
            continue
        if not begun:
            raise ValueError("MoleCode must start with graph TB or graph LR")
        if re.fullmatch(r'subgraph [A-Za-z][A-Za-z0-9_]*(?:\["[^"\r\n]*"\])?', line):
            depth += 1
            continue
        if line == "end":
            depth -= 1
            if depth < 0:
                raise ValueError("Unmatched end")
            continue
        node = NODE.fullmatch(line)
        if node:
            atom_id, label = node.groups()
            unknown_h = label.endswith(";H=?")
            if unknown_h:
                label = label[:-4]
            match = LABEL.fullmatch(label)
            if not match:
                raise ValueError(f"Invalid atom label on line {line_number}")
            isotope, element, hydrogen, charge_n, charge_sign = match.groups()
            graph["atoms"].append({"id": atom_id, "element": element, "isotope": int(isotope or 0),
                                   "hydrogens": None if unknown_h else (0 if hydrogen is None else int(hydrogen or 1)),
                                   "charge": int(charge_n or 1) * (1 if charge_sign == "+" else -1) if charge_sign else 0,
                                   "aromatic": False, "radicals": 0, "chiral_tag": 0, "atom_map": 0})
            continue
        edge = EDGE.fullmatch(line)
        if edge:
            a, operator, stereo_label, b = edge.groups()
            graph["bonds"].append({"a": a, "b": b, "order": next(k for k, v in OPERATORS.items() if v == operator),
                                   "stereo": 0, "stereo_atoms": []})
            if stereo_label:
                bond_labels[(a, b)] = stereo_label
            continue
        raise ValueError(f"Unrecognized MoleCode syntax on line {line_number}: {line}")
    if not begun or depth:
        raise ValueError("Missing graph header or unclosed subgraph")
    atom_ids = {atom["id"] for atom in graph["atoms"]}
    edge_ids = {(bond["a"], bond["b"]) for bond in graph["bonds"]}
    if set(atom_meta) - atom_ids or set(bond_meta) - edge_ids:
        raise ValueError("Metadata refers to a missing atom or bond")
    for atom in graph["atoms"]:
        atom.update(atom_meta.get(atom["id"], {}))
    for bond in graph["bonds"]:
        key = (bond["a"], bond["b"])
        bond.update(bond_meta.get(key, {}))
        if bond["order"] == 1.5:
            for atom in graph["atoms"]:
                if atom["id"] in key:
                    if atom["id"] in atom_meta and not atom["aromatic"]:
                        raise ValueError("Aromatic bond conflicts with explicit atom metadata")
                    atom["aromatic"] = True
        if key in bond_labels and {2: "Z", 3: "E"}.get(bond["stereo"]) != bond_labels[key]:
            raise ValueError("E/Z annotations require consistent explicit stereo-reference metadata")
    require_graph(graph)
    return graph

