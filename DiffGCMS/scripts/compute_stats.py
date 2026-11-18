"""Compute dataset marginal statistics from jsonl without loading full DataLoader."""

from __future__ import annotations

import argparse
import json
import os

import numpy as np
import torch
import torch.nn.functional as F

from src.datasets.graph_builder import DEFAULT_ATOM_DECODER, jsonl_to_graph


def compute_stats(jsonl_path: str, atom_decoder, stats_dir: str, max_samples: int | None = None, max_nodes: int = 50):
    os.makedirs(stats_dir, exist_ok=True)
    num_atom_types = len(atom_decoder)
    node_type_counts = torch.zeros(num_atom_types)
    edge_type_counts = torch.zeros(5)
    node_count_hist = torch.zeros(max_nodes + 1)
    valency_hist = torch.zeros(3 * max_nodes - 2)
    multiplier = torch.tensor([0, 1, 2, 3, 1.5])

    n = 0
    with open(jsonl_path) as f:
        for line in f:
            if max_samples is not None and n >= max_samples:
                break
            row = json.loads(line)
            if len(row["input_ids"]) > 300 or len(row["node_types"]) > max_nodes:
                continue
            graph = jsonl_to_graph(row, atom_decoder=atom_decoder)
            nn = graph.x.size(0)
            if nn >= node_count_hist.numel():
                continue
            node_count_hist[nn] += 1
            node_type_counts += graph.x.sum(dim=0)
            if graph.edge_index.numel() > 0:
                edge_type_counts[1:] += graph.edge_attr.sum(dim=0)[1:]
            all_pairs = nn * (nn - 1)
            num_edges = graph.edge_index.size(1)
            edge_type_counts[0] += all_pairs - num_edges
            for atom in range(nn):
                edges = graph.edge_attr[graph.edge_index[0] == atom]
                val = (edges.sum(dim=0) * multiplier).sum()
                valency_hist[val.long().item()] += 1
            n += 1

    node_type_counts = node_type_counts / node_type_counts.sum()
    edge_type_counts = edge_type_counts / edge_type_counts.sum()
    if node_count_hist.sum() > 0:
        node_count_hist = node_count_hist / node_count_hist.sum()
    if valency_hist.sum() > 0:
        valency_hist = valency_hist / valency_hist.sum()

    np.savetxt(os.path.join(stats_dir, "atom_types.txt"), node_type_counts.numpy())
    np.savetxt(os.path.join(stats_dir, "edge_types.txt"), edge_type_counts.numpy())
    np.savetxt(os.path.join(stats_dir, "n_counts.txt"), node_count_hist.numpy())
    np.savetxt(os.path.join(stats_dir, "valencies.txt"), valency_hist.numpy())
    print(f"Computed stats from {n} samples -> {stats_dir}")
    print(f"m_E (none/single/double/triple/aromatic): {edge_type_counts.tolist()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--jsonl", required=True)
    parser.add_argument("--stats-dir", default="../dataset/split_90_5_5/stats")
    parser.add_argument(
        "--max-samples",
        type=int,
        default=None,
        help="If set, stop after this many accepted samples. Default: full file.",
    )
    parser.add_argument("--max-nodes", type=int, default=50)
    args = parser.parse_args()
    compute_stats(args.jsonl, DEFAULT_ATOM_DECODER, args.stats_dir, args.max_samples, args.max_nodes)
