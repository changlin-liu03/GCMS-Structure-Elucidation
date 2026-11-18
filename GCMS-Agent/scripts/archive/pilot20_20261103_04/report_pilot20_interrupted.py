"""Freeze a user-stopped pilot, then evaluate saved predictions without API calls.

Keep the original pending ledger intact: its reservation is not a known invoice.
Only exported results enter the paired accuracy comparison; an interrupted case
and never-started cases are explicitly separate.
"""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT / "src"))
from gcms_agent.config import write_json
from gcms_agent.data.loader import load_inputs
from gcms_agent.workflow.runner import source_versions
from report_pilot20 import assess

RUN = PROJECT / "runs/pilot20_v31_20261104"
BASE = PROJECT / "runs/pilot20_v31_baseline_20261104"
OUT = PROJECT / "runs/pilot20_v31_report_20261104"
OLD = PROJECT / "runs/economy_20261103_optimized"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def inventory():
    paths = [RUN / name for name in ("manifest.json", "input.json", "protocol.json", "tested_source.zip", "logs/budget.json")]
    paths += list((RUN / "results").glob("*.json")) + list((RUN / "checkpoints").glob("*.json"))
    paths += [BASE / "manifest.json"] + list((BASE / "results").glob("*.json"))
    return {str(p): sha(p) for p in sorted(paths)}


def main():
    if (OUT / "interrupted_prediction_freeze.json").exists():
        raise ValueError("Interrupted report already frozen; do not overwrite it")
    manifest, ledger = read(RUN / "manifest.json"), read(RUN / "logs/budget.json")
    if source_versions() != manifest["versions"]:
        raise ValueError("Scientific source changed")
    baseline_manifest = read(BASE / "manifest.json")
    if baseline_manifest["versions"] != manifest["versions"] or baseline_manifest["config_hash"] != manifest["config_hash"]:
        raise ValueError("Baseline scientific versions/config differ")
    rows = load_inputs(RUN / "input.json")
    results = {r["sample_id"]: r for p in (RUN / "results").glob("*.json") for r in [read(p)]}
    processed_rows = [r for r in rows if r["sample_id"] in results]
    attempted = {r["sample_id"] for r in ledger["requests"]}
    attempted.update(v["sample_id"] for v in ledger.get("reservation_metadata", {}).values())
    interrupted = sorted(attempted - results.keys())
    unstarted = [r["sample_id"] for r in rows if r["sample_id"] not in attempted]
    frozen = {"created_utc": datetime.now(timezone.utc).isoformat(), "stop_reason": "explicit_user_request",
        "process_exit_code": 1, "matching_target_processes_after_stop": 0,
        "labels_read": False, "scientific_source_unchanged": True,
        "files": inventory(), "script_sha256": sha(__file__),
        "pending_accounting_policy": "Original pending reservation retained; no fabricated usage and no resumed API calls"}
    write_json(OUT / "interrupted_prediction_freeze.json", frozen)
    if inventory() != frozen["files"]:
        raise ValueError("Prediction files changed after stop/freeze")
    # This is the first label access, after prediction and ledger freeze.
    from gcms_agent.evaluation.metrics import canonical
    from gcms_agent.evaluation.baselines import original_order_metrics
    labels = read(PROJECT / "data/evaluation/labels.json")
    truth = {r["sample_id"]: canonical(r["true_smiles"]) for r in labels}
    agent, cases = assess(processed_rows, results, truth)
    base_results = {r["sample_id"]: r for p in (BASE / "results").glob("*.json") for r in [read(p)]}
    baseline, _ = assess(processed_rows, base_results, truth)
    old_results = {}
    for row in processed_rows:
        sid = row["sample_id"]
        paths = [p for p in (OLD / "results" / f"{sid}.json", OLD / "continuation/shards" / sid / "results" / f"{sid}.json") if p.exists()]
        if len(paths) != 1:
            raise ValueError("Ambiguous old paired case")
        old_results[sid] = read(paths[0])
    old, _ = assess(processed_rows, old_results, truth)
    failures = [read(p) for p in (RUN / "checkpoints").glob("*_failure.json")]
    by_case = {}
    components = {"input_cny": 0., "cache_cny": 0., "output_cny": 0.}
    fields = {"input_tokens": "input", "cache_read_input_tokens": "cache", "output_tokens": "output"}
    for request in ledger["requests"]:
        item = by_case.setdefault(request["sample_id"], {"known_cost_cny": 0., "sessions": 0})
        item["known_cost_cny"] += request["cost_cny"]
        item["sessions"] += 1
        if request["usage"].get("known"):
            prices = ledger["prices_cny"][request["charged_tariff"]]
            for field, key in fields.items():
                components[key + "_cny"] += request["usage"].get(field, 0) * prices[key] / 1e6
    known_saved_cost = sum(by_case[sid]["known_cost_cny"] for sid in results)
    pending = sum(ledger.get("pending_reservations", {}).values())
    quality = Counter()
    for result in results.values():
        q = result.get("explanation_quality", {})
        profile = q.get("evidence_profile", {})
        quality.update(q.get("limitations", []))
        quality["top1_present"] += result.get("top1") is not None
        quality["point_top1_tied"] += result.get("ranking_diagnostics", {}).get("top_score_candidate_count", 0) > 1
        quality["top1_coverage_below_50_percent"] += bool(result.get("top1")) and profile.get("rule_supported_intensity_fraction", 0) < .5
    old_paired_cost = sum(r["cost_cny"] for p in OLD.glob("**/logs/budget.json") for r in read(p)["requests"] if r.get("sample_id") in results)
    report = {"created_utc": datetime.now(timezone.utc).isoformat(), "status": "stopped_by_user",
        "evaluation_scope": "Previously inspected development cases; partial pilot, not unseen holdout or complete 20-case benchmark",
        "requested_cases": len(rows), "saved_result_cases": len(results),
        "workflow_complete_cases": agent["complete_cases"], "workflow_incomplete_cases": len(results) - agent["complete_cases"],
        "interrupted_without_export": interrupted, "unstarted_sample_ids": unstarted,
        "accuracy_denominator_policy": "Paired metrics below use only the same saved-result cases; interrupted/unstarted cases are not silently called model failures",
        "agent_saved_cases": agent, "old_agent_same_saved_cases": old,
        "baseline_same_saved_cases": baseline, "original_diffusion_same_saved_cases": original_order_metrics(processed_rows, labels),
        "cost": {"known_settled_cny": ledger["spent_cny"], "settled_sessions": ledger["sessions"],
            "settled_unknown_usage_sessions": ledger["unknown_usage_sessions"],
            "interrupted_unsettled_sessions": len(ledger.get("pending_reservations", {})),
            "interrupted_reservation_cny": pending, "known_plus_reservation_cny": ledger["spent_cny"] + pending,
            "known_saved_cases_cny": known_saved_cost,
            "known_cost_on_interrupted_case_cny": ledger["spent_cny"] - known_saved_cost,
            "old_same_saved_cases_cny": old_paired_cost,
            "usage_known_sessions_only": ledger["usage"], "known_cost_components": components,
            "by_case": by_case, "limit_cny": 10,
            "note": "Token-based project accounting, not a provider invoice. Pending reservation is an estimate, not confirmed cost or guaranteed upper bound.",
            "population_projection": None, "projection_blockers": ["user_stopped_partial_pilot", "incomplete_cases", "one_interrupted_unsettled_request"]},
        "failure_receipts": dict(Counter(f["error_category"] for f in failures)),
        "quality_flags_saved_cases": dict(quality), "cases": cases,
        "labels_used_only_after_freeze": True, "labels_sha256": sha(PROJECT / "data/evaluation/labels.json"),
        "prediction_freeze": str(OUT / "interrupted_prediction_freeze.json"),
    }
    if inventory() != frozen["files"] or source_versions() != manifest["versions"]:
        raise ValueError("Frozen sources or predictions changed during evaluation")
    write_json(OUT / "interrupted_report.json", report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
