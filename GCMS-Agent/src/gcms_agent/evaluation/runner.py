import json
from pathlib import Path
from gcms_agent.config import write_json
from gcms_agent.evaluation.metrics import evaluate_results

def evaluate(run_dir, labels_path, inputs_path=None):
    root = Path(run_dir)
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((root/"results").glob("*.json"))]
    complete = [r for r in rows if r["ranking_complete"]]
    labels = json.loads(Path(labels_path).read_text(encoding="utf-8"))
    result = {"processed": evaluate_results(rows, labels), "complete_only": evaluate_results(complete, labels), "incomplete_samples": len(rows)-len(complete)}
    manifest = json.loads((root/"manifest.json").read_text(encoding="utf-8"))
    expected = set(manifest["sample_ids"])
    available = {r["sample_id"] for r in rows}
    label_ids = {r["sample_id"] for r in labels}
    if len(label_ids) != len(labels):
        raise ValueError("Duplicate evaluation label IDs")
    if expected - label_ids:
        raise ValueError("Evaluation labels are missing requested sample IDs")
    from gcms_agent.evaluation.metrics import canonical
    if any(canonical(r["true_smiles"]) is None for r in labels if r["sample_id"] in expected):
        raise ValueError("Evaluation label contains an invalid reference SMILES")
    # Missing predictions are failures in the requested-set accuracy denominator.
    requested_rows = rows + [{"sample_id":sid,"ranked_candidates":[],"unique_ranking":[]} for sid in sorted(expected-available)]
    result["requested_set"] = evaluate_results(requested_rows, labels)
    strict_rows = [r if r.get("ranking_complete", False) else
                   {**r, "unique_ranking": [], "top1": None} for r in requested_rows]
    result["strict_end_to_end_requested_set"] = evaluate_results(strict_rows, labels)
    result["accuracy_policy"] = "requested_set includes provisional rankings; strict_end_to_end_requested_set counts incomplete/unprocessed cases as misses; null top1 is abstention"
    result["requested_samples"] = len(expected)
    result["unprocessed_samples"] = len(expected-available)
    if inputs_path:
        from gcms_agent.data.loader import load_inputs
        from gcms_agent.evaluation.baselines import original_order_metrics
        inputs = [r for r in load_inputs(inputs_path) if r["sample_id"] in expected]
        result["original_diffusion_order"] = original_order_metrics(inputs, labels)
    write_json(root / "evaluation.json", result)
    return result
