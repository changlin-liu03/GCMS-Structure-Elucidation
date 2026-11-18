from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

import numpy as np
import torch
from torch.utils.data import DataLoader, Dataset
from torch_geometric.data import Batch

from src.datasets.abstract_dataset import (
    ATOM_TO_VALENCY,
    ATOM_TO_WEIGHT,
    AbstractDatasetInfos,
    MolecularDataModule,
)
from src.datasets.graph_builder import jsonl_to_graph, scan_atom_types
import src.utils as utils


class GCMSJsonlDataset(Dataset):
    """Random-access jsonl via byte offsets. File handle is per-process for DataLoader workers."""

    def __init__(
        self,
        jsonl_path: str,
        atom_decoder: List[str],
        max_num_peaks: int = 300,
        max_nodes: int = 50,
        validate_formula: bool = False,
        max_samples: Optional[int] = None,
    ):
        self.jsonl_path = jsonl_path
        self.atom_decoder = atom_decoder
        self.max_num_peaks = max_num_peaks
        self.max_nodes = max_nodes
        self.validate_formula = validate_formula
        self.max_samples = max_samples
        self.line_indices: List[int] = []
        self.line_offsets: List[int] = []
        self._fh = None
        self._load_index()

    def __getstate__(self):
        state = self.__dict__.copy()
        state["_fh"] = None
        return state

    def __len__(self) -> int:
        return len(self.line_indices)

    def _cache_path(self) -> str:
        return f"{self.jsonl_path}.offsets.npz"

    def _stat_signature(self):
        st = os.stat(self.jsonl_path)
        return int(st.st_mtime), int(st.st_size)

    def _load_index(self) -> None:
        mtime, size = self._stat_signature()
        cache_path = self._cache_path()
        max_samples = -1 if self.max_samples is None else int(self.max_samples)
        if os.path.exists(cache_path):
            cached = np.load(cache_path)
            if (
                int(cached["mtime"]) == mtime
                and int(cached["size"]) == size
                and int(cached["max_num_peaks"]) == self.max_num_peaks
                and int(cached["max_nodes"]) == self.max_nodes
                and int(cached["max_samples"]) == max_samples
            ):
                self.line_indices = cached["line_indices"].astype(np.int64).tolist()
                self.line_offsets = cached["line_offsets"].astype(np.int64).tolist()
                return
        self._build_index()
        np.savez(
            cache_path,
            line_indices=np.asarray(self.line_indices, dtype=np.int64),
            line_offsets=np.asarray(self.line_offsets, dtype=np.int64),
            mtime=np.int64(mtime),
            size=np.int64(size),
            max_num_peaks=np.int64(self.max_num_peaks),
            max_nodes=np.int64(self.max_nodes),
            max_samples=np.int64(max_samples),
        )

    def _build_index(self) -> None:
        self.line_indices = []
        self.line_offsets = []
        with open(self.jsonl_path, "rb") as f:
            line_no = 0
            while True:
                offset = f.tell()
                raw = f.readline()
                if not raw:
                    break
                if self.max_samples is not None and len(self.line_indices) >= self.max_samples:
                    break
                row = json.loads(raw)
                if len(row["input_ids"]) <= self.max_num_peaks and len(row["node_types"]) <= self.max_nodes:
                    self.line_indices.append(line_no)
                    self.line_offsets.append(offset)
                line_no += 1

    def _file(self):
        fh = getattr(self, "_fh", None)
        if fh is None or fh.closed:
            self._fh = open(self.jsonl_path, "rb")
        return self._fh

    def _read_row(self, idx: int) -> dict:
        fh = self._file()
        fh.seek(self.line_offsets[idx])
        return json.loads(fh.readline())

    def __getitem__(self, idx: int) -> dict:
        row = self._read_row(idx)
        graph = jsonl_to_graph(
            row,
            atom_decoder=self.atom_decoder,
            validate_formula=self.validate_formula,
        )
        return {
            "input_ids": torch.tensor(row["input_ids"], dtype=torch.long),
            "position_ids": torch.tensor(row["position_ids"], dtype=torch.long),
            "graph": graph,
            "raw": row,
            "jsonl_index": self.line_indices[idx],
        }


def _collate_spectrum(batch: List[dict]) -> dict:
    input_ids = [item["input_ids"] for item in batch]
    position_ids = [item["position_ids"] for item in batch]
    max_len = max(x.size(0) for x in input_ids)

    padded_input = []
    padded_pos = []
    attention_mask = []
    for ids, pos in zip(input_ids, position_ids):
        seq_len = ids.size(0)
        pad_len = max_len - seq_len
        if pad_len > 0:
            ids = torch.cat([ids, torch.zeros(pad_len, dtype=torch.long)])
            pos = torch.cat([pos, torch.zeros(pad_len, dtype=torch.long)])
        mask = torch.zeros(max_len, dtype=torch.long)
        mask[:seq_len] = 1
        padded_input.append(ids)
        padded_pos.append(pos)
        attention_mask.append(mask)

    return {
        "input_ids": torch.stack(padded_input),
        "position_ids": torch.stack(padded_pos),
        "attention_mask": torch.stack(attention_mask),
    }


def _collate_pairs(batch: List[dict]) -> dict:
    spectrum = _collate_spectrum(batch)
    graph = Batch.from_data_list([item["graph"] for item in batch])
    return {
        "spectrum": spectrum,
        "graph": graph,
        "raw": [item["raw"] for item in batch],
        "jsonl_index": torch.tensor([item["jsonl_index"] for item in batch], dtype=torch.long),
    }


class GCMSDataModule(MolecularDataModule):
    def __init__(self, cfg):
        self.remove_h = True
        root = os.path.join(cfg.general.parent_dir, cfg.dataset.datadir)
        self.datadir = root
        self.train_smiles: List[str] = []
        atom_decoder = list(cfg.dataset.atom_decoder)

        train_path = os.path.join(self.datadir, cfg.dataset.train_file)
        if not atom_decoder:
            atom_decoder = scan_atom_types(train_path)
            cfg.dataset.atom_decoder = atom_decoder

        common = dict(
            atom_decoder=atom_decoder,
            max_num_peaks=cfg.dataset.max_num_peaks,
            max_nodes=cfg.dataset.max_nodes,
            validate_formula=getattr(cfg.dataset, "validate_formula", False),
            max_samples=getattr(cfg.dataset, "max_train_samples", None),
        )

        val_common = dict(common)
        val_common["max_samples"] = getattr(cfg.dataset, "max_val_samples", None)
        test_common = dict(common)
        test_common["max_samples"] = getattr(cfg.dataset, "max_test_samples", None)

        datasets = {
            "train": GCMSJsonlDataset(os.path.join(self.datadir, cfg.dataset.train_file), **common),
            "val": GCMSJsonlDataset(os.path.join(self.datadir, cfg.dataset.valid_file), **val_common),
            "test": GCMSJsonlDataset(os.path.join(self.datadir, cfg.dataset.test_file), **test_common),
        }
        super().__init__(cfg, datasets)
        self.atom_decoder = atom_decoder
        self.kwargs["collate_fn"] = _collate_pairs
        if self.kwargs.get("num_workers", 0) > 0:
            self.kwargs["persistent_workers"] = True
            self.kwargs["prefetch_factor"] = 4

    def train_dataloader(self) -> DataLoader:
        return DataLoader(
            self.train_dataset,
            shuffle=True,
            batch_size=self.batch_size,
            **self.kwargs,
        )

    def val_dataloader(self) -> DataLoader:
        kwargs = dict(self.kwargs)
        return DataLoader(
            self.val_dataset,
            shuffle=False,
            batch_size=self.eval_batch_size,
            **kwargs,
        )

    def test_dataloader(self) -> DataLoader:
        kwargs = dict(self.kwargs)
        return DataLoader(
            self.test_dataset,
            shuffle=False,
            batch_size=self.eval_batch_size,
            **kwargs,
        )

    def valency_count(self, max_n_nodes):
        valencies = torch.zeros(3 * max_n_nodes - 2)
        multiplier = torch.tensor([0, 1, 2, 3, 1.5])

        for batch in self.train_dataloader():
            data = batch["graph"]
            n = data.x.shape[0]
            for atom in range(n):
                edges = data.edge_attr[data.edge_index[0] == atom]
                edges_total = edges.sum(dim=0)
                valency = (edges_total * multiplier).sum()
                valencies[valency.long().item()] += 1
        valencies = valencies / valencies.sum()
        return valencies

    def node_counts(self, max_nodes_possible=150):
        all_counts = torch.zeros(max_nodes_possible)
        for loader in [self.train_dataloader(), self.val_dataloader()]:
            for batch in loader:
                data = batch["graph"]
                unique, counts = torch.unique(data.batch, return_counts=True)
                for count in counts:
                    all_counts[count] += 1
        max_index = max(all_counts.nonzero())
        all_counts = all_counts[: max_index + 1]
        all_counts = all_counts / all_counts.sum()
        return all_counts

    def node_types(self):
        num_classes = None
        for batch in self.train_dataloader():
            data = batch["graph"]
            num_classes = data.x.shape[1]
            break

        counts = torch.zeros(num_classes)
        for batch in self.train_dataloader():
            data = batch["graph"]
            counts += data.x.sum(dim=0)
        counts = counts / counts.sum()
        return counts

    def edge_counts(self):
        num_classes = None
        for batch in self.train_dataloader():
            data = batch["graph"]
            num_classes = data.edge_attr.shape[1]
            break

        d = torch.zeros(num_classes, dtype=torch.float)
        for batch in self.train_dataloader():
            data = batch["graph"]
            unique, counts = torch.unique(data.batch, return_counts=True)
            all_pairs = 0
            for count in counts:
                all_pairs += count * (count - 1)
            num_edges = data.edge_index.shape[1]
            num_non_edges = all_pairs - num_edges
            edge_types = data.edge_attr.sum(dim=0)
            d[0] += num_non_edges
            d[1:] += edge_types[1:]
        d = d / d.sum()
        return d


class GCMSDatasetInfos(AbstractDatasetInfos):
    def __init__(self, datamodule: GCMSDataModule, cfg, recompute_statistics=False, meta=None):
        self.name = "gcms"
        self.input_dims = None
        self.output_dims = None
        self.remove_h = True

        self.atom_decoder = list(datamodule.atom_decoder)
        self.atom_encoder = {atom: i for i, atom in enumerate(self.atom_decoder)}
        self.atom_weights = {i: ATOM_TO_WEIGHT.get(atom, 0) for i, atom in enumerate(self.atom_decoder)}
        self.valencies = [ATOM_TO_VALENCY.get(atom, 0) for atom in self.atom_decoder]
        self.num_atom_types = len(self.atom_decoder)
        self.max_weight = max(self.atom_weights.values()) if self.atom_weights else 0
        self._root_path = os.path.join(cfg.general.parent_dir, cfg.dataset.datadir)
        stats_dir = getattr(cfg.dataset, "stats_dir", None) or os.path.join(self._root_path, "stats")
        if not os.path.isabs(stats_dir):
            stats_dir = os.path.join(cfg.general.parent_dir, stats_dir.lstrip("./"))
        os.makedirs(stats_dir, exist_ok=True)

        meta_files = dict(
            n_nodes=os.path.join(stats_dir, "n_counts.txt"),
            node_types=os.path.join(stats_dir, "atom_types.txt"),
            edge_types=os.path.join(stats_dir, "edge_types.txt"),
            valency_distribution=os.path.join(stats_dir, "valencies.txt"),
        )

        self.n_nodes = None
        self.node_types = None
        self.edge_types = None
        self.valency_distribution = None

        if meta is None:
            meta = dict(n_nodes=None, node_types=None, edge_types=None, valency_distribution=None)

        for k, path in meta_files.items():
            if (meta.get(k) is None) and os.path.exists(path):
                meta[k] = torch.tensor(np.loadtxt(path))
                setattr(self, k, meta[k])

        self.max_n_nodes = len(self.n_nodes) - 1 if self.n_nodes is not None else None

        if recompute_statistics or self.n_nodes is None:
            self.n_nodes = datamodule.node_counts()
            np.savetxt(meta_files["n_nodes"], self.n_nodes.numpy())
            self.max_n_nodes = len(self.n_nodes) - 1
        if recompute_statistics or self.node_types is None:
            self.node_types = datamodule.node_types()
            np.savetxt(meta_files["node_types"], self.node_types.numpy())
        if recompute_statistics or self.edge_types is None:
            self.edge_types = datamodule.edge_counts()
            np.savetxt(meta_files["edge_types"], self.edge_types.numpy())
        if recompute_statistics or self.valency_distribution is None:
            valencies = datamodule.valency_count(self.max_n_nodes)
            np.savetxt(meta_files["valency_distribution"], valencies.numpy())
            self.valency_distribution = valencies

        self.complete_infos(n_nodes=self.n_nodes, node_types=self.node_types)

    def compute_input_output_dims(self, datamodule, extra_features, domain_features):
        example_batch = next(iter(datamodule.train_dataloader()))
        graph = example_batch["graph"]
        ex_dense, node_mask = utils.to_dense(
            graph.x, graph.edge_index, graph.edge_attr, graph.batch
        )
        y_dim = getattr(datamodule.cfg.model, "y_dim", 1024)
        y = torch.zeros(graph.num_graphs, y_dim)
        example_data = {
            "X_t": ex_dense.X,
            "E_t": ex_dense.E,
            "y_t": y,
            "node_mask": node_mask,
        }

        self.input_dims = {
            "X": graph.x.size(1),
            "E": graph.edge_attr.size(1),
            "y": y_dim + 1,
        }

        ex_extra_feat = extra_features(example_data)
        self.input_dims["X"] += ex_extra_feat.X.size(-1)
        self.input_dims["E"] += ex_extra_feat.E.size(-1)
        self.input_dims["y"] += ex_extra_feat.y.size(-1)

        ex_extra_molecular_feat = domain_features(example_data)
        self.input_dims["X"] += ex_extra_molecular_feat.X.size(-1)
        self.input_dims["E"] += ex_extra_molecular_feat.E.size(-1)
        self.input_dims["y"] += ex_extra_molecular_feat.y.size(-1)

        self.output_dims = {
            "X": graph.x.size(1),
            "E": graph.edge_attr.size(1),
            "y": y_dim,
        }
