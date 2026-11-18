"""Select fixed development inputs directly from the current prepared dataset."""
import argparse
import json
from pathlib import Path

from gcms_agent.config import DEFAULT_DATA_ROOT, PROJECT_ROOT, write_json, digest
from gcms_agent.data.loader import load_inputs


def prepare_cases(data_root, source, single_index=0, pilot_indices=(85, 120, 294, 313)):
    import hashlib
    data_root, source = Path(data_root), Path(source)
    rows = load_inputs(data_root / "inference/all.json")
    manifest = json.loads((data_root / "manifests/data.json").read_text(encoding="utf-8"))
    if hashlib.sha256(source.read_bytes()).hexdigest() != manifest["source_sha256"]:
        raise ValueError("Source differs from prepared dataset")
    indices = [single_index, *pilot_indices]
    if any(type(i) is not int or i < 0 or i >= len(rows) for i in indices):
        raise ValueError("Development indices must reference the current dataset")
    row = rows[single_index]
    labels = json.loads((data_root / "evaluation/labels.json").read_text(encoding="utf-8"))
    write_json(data_root / "inference/single_top10_miss.json", [row])
    write_json(data_root / "evaluation/single_top10_miss_labels.json",
               [label for label in labels if label["sample_id"] == row["sample_id"]])
    write_json(data_root / "manifests/single_top10_miss_selection.json", {
        "selection_rule": "Fixed development source index; no truth-based selection; not held out",
        "source": str(source.resolve()), "source_sha256": manifest["source_sha256"],
        "source_index_zero_based": single_index, "sample_id": row["sample_id"],
        "inference_input_hash": digest([row]), "intensity_encoding": "raw_linear",
        "inference_input_has_truth": False})
    pilot = [rows[i] for i in pilot_indices]
    write_json(data_root / "inference/engineering_pilot.json", pilot)
    write_json(data_root / "manifests/engineering_pilot.json", {
        "source_indices_zero_based": list(pilot_indices), "input_hash": digest(pilot),
        "selection_rule": "Fixed development indices; no truth-based selection; not held out"})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--source", type=Path, default=PROJECT_ROOT.parent / "test.json")
    parser.add_argument("--single-index", type=int, default=0)
    parser.add_argument("--pilot-indices", type=int, nargs="+", default=[85, 120, 294, 313])
    args = parser.parse_args()
    prepare_cases(args.data_root, args.source, args.single_index, args.pilot_indices)
