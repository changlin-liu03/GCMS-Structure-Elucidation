"""Trusted data preparation. This module is never exposed as an agent tool."""
import json
import hashlib
from pathlib import Path
from collections import Counter
from rdkit import Chem, RDLogger
from gcms_agent.config import digest, write_json
from gcms_agent.data.loader import validate_input
from gcms_agent.data.intensity import RAW_ENCODING, LEGACY_INPUT_ERROR

def prepare(source, destination):
    RDLogger.DisableLog("rdApp.error")
    source_bytes = Path(source).read_bytes()
    raw = json.loads(source_bytes.decode("utf-8-sig"))
    if not isinstance(raw, list) or not raw:
        raise ValueError("Source must be a nonempty JSON array")
    destination = Path(destination)
    if any((destination / name).exists() for name in (
            "inference/all.json", "inference/pilot.json", "evaluation/labels.json", "manifests/data.json")):
        raise ValueError("Prepared data already exists; choose a new data directory")
    rows, labels, strata = [], [], {}
    counts = Counter()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"Source row {index} must be an object")
        if "position_ids" in item or "intensity" not in item:
            raise ValueError(LEGACY_INPUT_ERROR)
        encoding = RAW_ENCODING
        row = {k: item[k] for k in ("input_ids", "intensity", "generated")}
        identity = [row["input_ids"], row["intensity"], encoding]
        row["sample_id"] = f"s{index:05d}_{digest(identity)[:12]}"
        row["conditions"] = {"ionization": "EI", "energy_ev": 70, "energy_is_assumed": True, "resolution": "nominal", "intensity_encoding": encoding}
        if "conditions" in item:
            if not isinstance(item["conditions"], dict):
                raise ValueError(f"Source row {index}: conditions must be an object")
            row["conditions"].update(item["conditions"])
        validate_input(row)
        rows.append(row)
        if "true_smiles" in item:
            labels.append({"sample_id": row["sample_id"], "true_smiles": item["true_smiles"]})
        valid, disconnected, unparseable = 0, 0, 0
        for s in row["generated"]:
            mol = Chem.MolFromSmiles(s)
            if mol is not None:
                valid += 1
                disconnected += int(len(Chem.GetMolFrags(mol)) > 1)
            elif Chem.MolFromSmiles(s, sanitize=False) is None:
                unparseable += 1
        duplicate = len(set(row["generated"])) < 10
        group = "all_invalid" if valid == 0 else "disconnected" if disconnected else "partly_invalid" if valid < 10 else "duplicates" if duplicate else "valid_unique"
        strata.setdefault(group, []).append(row["sample_id"])
        counts.update({"samples": 1, "candidates": 10, "rdkit_invalid": 10-valid, "disconnected": disconnected, "syntax_unparseable": unparseable, "duplicate_samples": int(duplicate)})
    # Deterministic round-robin stratification, independent of the labels.
    pilot = []
    groups = [strata.get(g, []) for g in ("all_invalid", "disconnected", "partly_invalid", "duplicates", "valid_unique")]
    for offset in range(max(map(len, groups), default=0)):
        for group in groups:
            if offset < len(group) and len(pilot) < 20:
                pilot.append(group[offset])
        if len(pilot) >= 20:
            break
    destination = Path(destination)
    write_json(destination / "inference" / "all.json", rows)
    write_json(destination / "inference" / "pilot.json", [r for r in rows if r["sample_id"] in pilot])
    write_json(destination / "evaluation" / "labels.json", labels)
    manifest = {"schema_version": "raw_linear_v1", "intensity_encoding": RAW_ENCODING,
                "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
                "normalization": {"display": "none; original input intensity", "weight": "I/sum(I)", "zero_policy": "not_observed"},
                "counts": dict(counts), "input_hash": digest(rows), "pilot_ids": pilot,
                "strata_counts": {k: len(v) for k,v in strata.items()}}
    write_json(destination / "manifests" / "data.json", manifest)
    return manifest
