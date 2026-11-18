"""Build PyG molecular graphs from GCMS jsonl records."""

from __future__ import annotations

import re
from typing import Dict, List, Sequence, Tuple

import torch
import torch.nn.functional as F
from torch_geometric.data import Data

DEFAULT_ATOM_DECODER = ["C", "N", "O", "F", "Cl", "S", "P", "Si", "Br", "I"]
NUM_EDGE_TYPES = 5  # 0=no bond, 1=single, 2=double, 3=triple, 4=aromatic


def build_atom_encoder(atom_decoder: Sequence[str]) -> Dict[str, int]:
    return {atom: i for i, atom in enumerate(atom_decoder)}


def parse_formula(formula: str) -> Dict[str, int]:
    pattern = re.compile(r"([A-Z][a-z]?)(\d*)")
    counts: Dict[str, int] = {}
    for element, count in pattern.findall(formula):
        counts[element] = counts.get(element, 0) + (int(count) if count else 1)
    return counts


def heavy_atom_count_from_formula(formula: str, atom_decoder: Sequence[str]) -> int:
    counts = parse_formula(formula)
    return sum(counts.get(atom, 0) for atom in atom_decoder)


def jsonl_to_graph(
    row: dict,
    atom_decoder: Sequence[str] = DEFAULT_ATOM_DECODER,
    validate_formula: bool = False,
) -> Data:
    atom_encoder = build_atom_encoder(atom_decoder)
    node_types: List[str] = row["node_types"]
    atom_hydrogens: List[int] = row.get("atom_hydrogens", [0] * len(node_types))

    if len(atom_hydrogens) != len(node_types):
        raise ValueError(
            f"atom_hydrogens length {len(atom_hydrogens)} != node_types length {len(node_types)}"
        )

    type_idx = []
    for atom in node_types:
        if atom not in atom_encoder:
            raise ValueError(f"Unknown atom type {atom!r}; expected one of {atom_decoder}")
        type_idx.append(atom_encoder[atom])

    x = F.one_hot(torch.tensor(type_idx, dtype=torch.long), num_classes=len(atom_decoder)).float()

    edge_index_raw = row.get("edge_index", [])
    edge_types_raw = row.get("edge_types", [])

    if len(edge_index_raw) == 0:
        edge_index = torch.zeros((2, 0), dtype=torch.long)
        edge_attr = torch.zeros((0, NUM_EDGE_TYPES), dtype=torch.float)
    else:
        # COO format: [[src...], [dst...]]  (len == 2)
        # or list of pairs: [[i,j], [k,l], ...]  (len > 2, or single pair [[i,j]])
        if (
            len(edge_index_raw) == 2
            and isinstance(edge_index_raw[0], list)
            and isinstance(edge_index_raw[1], list)
            and (len(edge_index_raw[0]) == 0 or isinstance(edge_index_raw[0][0], int))
        ):
            src = list(edge_index_raw[0])
            dst = list(edge_index_raw[1])
        elif isinstance(edge_index_raw[0], list):
            src = [pair[0] for pair in edge_index_raw]
            dst = [pair[1] for pair in edge_index_raw]
        else:
            src = list(edge_index_raw[0])
            dst = list(edge_index_raw[1])

        if len(src) != len(dst):
            raise ValueError(f"edge_index src/dst length mismatch: {len(src)} vs {len(dst)}")
        if len(edge_types_raw) != len(src):
            raise ValueError(
                f"edge_types length {len(edge_types_raw)} != num edges {len(src)}"
            )

        edge_index = torch.tensor([src + dst, dst + src], dtype=torch.long)
        # Match edge order: forward bonds first, then reversed (not interleaved per bond)
        edge_type_vals = list(edge_types_raw) + list(edge_types_raw)
        for bond_type in edge_type_vals:
            if bond_type < 1 or bond_type >= NUM_EDGE_TYPES:
                raise ValueError(f"Invalid edge type {bond_type}; expected 1-{NUM_EDGE_TYPES - 1}")
        edge_type = torch.tensor(edge_type_vals, dtype=torch.long)
        edge_attr = F.one_hot(edge_type, num_classes=NUM_EDGE_TYPES).float()

    if validate_formula and "formula" in row:
        expected = heavy_atom_count_from_formula(row["formula"], atom_decoder)
        if expected != len(node_types):
            raise ValueError(
                f"Formula {row['formula']} implies {expected} heavy atoms, "
                f"but node_types has {len(node_types)}"
            )

    y = torch.zeros((1, 1), dtype=torch.float)

    return Data(
        x=x,
        edge_index=edge_index,
        edge_attr=edge_attr,
        y=y,
        smiles=row.get("smiles", ""),
        formula=row.get("formula", ""),
        inchi=row.get("inchi", ""),
        atom_hydrogens=torch.tensor(atom_hydrogens, dtype=torch.long),
    )


def scan_atom_types(jsonl_path: str) -> List[str]:
    import json

    seen = set()
    with open(jsonl_path) as f:
        for line in f:
            row = json.loads(line)
            seen.update(row["node_types"])
    ordered = [a for a in DEFAULT_ATOM_DECODER if a in seen]
    extras = sorted(seen - set(ordered))
    return ordered + extras
