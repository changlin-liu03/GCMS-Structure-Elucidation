"""Bounded development regression; freeze predictions before label evaluation.

Three fixed cases were selected for turn-limit, output-limit, and invalid-input
repair coverage. They are previously inspected development cases, not a new
accuracy test set. Default invocation is a keyless preflight; --execute is paid.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
from datetime import datetime, timezone

PROJECT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT / "src"))
from gcms_agent.config import digest, load_config, write_json
from gcms_agent.data.loader import load_inputs
from gcms_agent.workflow.runner import run_batch, source_versions
from run_economy_validation import STAGES, validation_lock

RUN = PROJECT / "runs/reliability_20261104_v3"
OLD = PROJECT / "runs/economy_20261103_optimized"
CAP = 3.0


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def selected_rows(sample_ids):
    rows = {r["sample_id"]: r for r in load_inputs(PROJECT / "data/inference/economy_pilot.json")}
    return [rows[sid] for sid in sample_ids]


def prior_commitments():
    total = 0.0
    for stage in STAGES:
        for path in (PROJECT / "runs" / f"economy_20261103_{stage}").glob("**/logs/budget.json"):
            ledger = read(path)
            if ledger.get("currency") != "CNY" or ledger.get("pending_reservations"):
                raise ValueError("Reconcile previous validation ledger before new paid calls")
            if ledger.get("unknown_usage_sessions", 0) or ledger.get("reservation_exceeded"):
                raise ValueError("Previous usage is not fully reconciled")
            total += ledger["spent_cny"]
    return total


def frozen_inventory():
    paths = [RUN / "manifest.json", RUN / "input.json", RUN / "protocol.json", RUN / "logs/budget.json"]
    paths += list((RUN / "results").glob("*.json"))
    paths += list((RUN / "checkpoints").glob("*.json"))
    paths += [RUN / "baseline/manifest.json", RUN / "baseline/logs/budget.json"]
    paths += list((RUN / "baseline/results").glob("*.json"))
    return {str(p): sha(p) for p in sorted(paths)}


def freeze():
    ledger = read(RUN / "logs/budget.json")
    if ledger.get("pending_reservations"):
        raise ValueError("Cannot freeze while a request is in flight")
    if source_versions() != read(RUN / "manifest.json")["versions"]:
        raise ValueError("Source changed during regression; do not mix versions")
    write_json(RUN / "prediction_freeze.json", {
        "utc": datetime.now(timezone.utc).isoformat(), "files": frozen_inventory(),
        "selection_uses_labels": False, "labels_read_during_this_run": False,
        "evaluation_scope": "Previously inspected development regression, not an unseen holdout",
        "script_sha256": sha(__file__),
    })


def evaluate_frozen():
    from gcms_agent.evaluation.runner import evaluate
    from gcms_agent.evaluation.metrics import evaluate_results
    frozen = read(RUN / "prediction_freeze.json")
    if frozen["files"] != frozen_inventory() or frozen["script_sha256"] != sha(__file__):
        raise ValueError("Frozen predictions or reporting script changed")
    if source_versions() != read(RUN / "manifest.json")["versions"]:
        raise ValueError("Scientific source changed after predictions")
    # The label file is first accessed only after the complete freeze check.
    labels_path = PROJECT / "data/evaluation/labels.json"
    evaluation = evaluate(RUN, labels_path, RUN / "input.json")
    baseline = evaluate(RUN / "baseline", labels_path, RUN / "input.json")
    labels = read(labels_path)
    sample_ids = [row["sample_id"] for row in load_inputs(RUN / "input.json")]
    old_results = []
    for sid in sample_ids:
        candidates = [OLD / "results" / f"{sid}.json",
                      OLD / "continuation/shards" / sid / "results" / f"{sid}.json"]
        paths = [p for p in candidates if p.exists()]
        if len(paths) != 1:
            raise ValueError("Ambiguous historical case result")
        result = read(paths[0])
        row = next(r for r in selected_rows(sample_ids) if r["sample_id"] == sid)
        if result["input_hash"] != digest(row):
            raise ValueError("Historical regression input differs")
        old_results.append(result)
    ledger = read(RUN / "logs/budget.json")
    current = [read(p) for p in sorted((RUN / "results").glob("*.json"))]
    old_cost = sum(request["cost_cny"] for path in OLD.glob("**/logs/budget.json")
                   for request in read(path)["requests"] if request.get("sample_id") in sample_ids)
    report = {"scope": "Development regression; do not extrapolate cost or accuracy to 1490 cases",
        "selected_sample_ids": sample_ids, "requested_samples": len(sample_ids),
        "old_complete": sum(r["ranking_complete"] for r in old_results),
        "new_complete": sum(r["ranking_complete"] for r in current),
        "old_subset_cost_cny_including_original_recovery": old_cost,
        "new_cost_cny": ledger["spent_cny"], "new_sessions": ledger["sessions"],
        "unknown_usage_sessions": ledger["unknown_usage_sessions"],
        "old_provisional_metrics": evaluate_results(old_results, labels),
        "old_strict_metrics": evaluate_results([r if r["ranking_complete"] else
            {**r, "top1": None, "unique_ranking": []} for r in old_results], labels),
        "new_evaluation": evaluation, "new_baseline": baseline,
        "cases": [{"sample_id": r["sample_id"], "complete": r["ranking_complete"],
            "stop_reasons": sorted({c["stop_reason"] for c in r["ranked_candidates"]}),
            "valid_outputs": sum(c.get("final_smiles") is not None for c in r["ranked_candidates"]),
            "ranking_diagnostics": r.get("ranking_diagnostics"), "resource_usage": r["resource_usage"]}
            for r in current]}
    write_json(RUN / "comparison.json", report)
    print(json.dumps({k: report[k] for k in ("requested_samples", "old_complete", "new_complete", "new_cost_cny", "new_sessions")}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--execute", action="store_true")
    actions.add_argument("--evaluate", action="store_true")
    parser.add_argument("--sample-ids", nargs=3, metavar="ID",
                        help="Local case IDs in turn-limit, output-limit, repair order; required except for --evaluate")
    args = parser.parse_args()
    if args.evaluate:
        if args.sample_ids:
            parser.error("--evaluate reads the saved input; omit --sample-ids")
        evaluate_frozen()
        return
    if not args.sample_ids or len(set(args.sample_ids)) != 3:
        parser.error("provide three distinct local case IDs with --sample-ids")
    sample_ids = args.sample_ids
    with validation_lock(PROJECT / "runs/economy_validation.lock"):
        rows, config = selected_rows(sample_ids), load_config()
        other = prior_commitments()
        if other + CAP > 27:
            raise ValueError("Insufficient remaining combined validation budget")
        config["runtime"].update(budget_currency="CNY", budget_cny=CAP, offpeak_only=True)
        if not args.execute:
            print(json.dumps({"paid_calls": False, "sample_ids": sample_ids, "budget_cny": CAP,
                "prior_validation_spent_cny": other, "runtime": config["runtime"]}, ensure_ascii=False))
            return
        if (RUN / "manifest.json").exists():
            raise ValueError("Regression already attempted; no silent resampling or overwrite")
        write_json(RUN / "input.json", rows)
        write_json(RUN / "protocol.json", {"budget_cny": CAP, "sample_ids": sample_ids,
            "selection_reasons": ["turn limit", "output limit", "invalid structure repair regression"],
            "labels_given_to_model": False, "scope": "development regression", "script_sha256": sha(__file__)})
        asyncio.run(run_batch(RUN / "input.json", RUN / "baseline", config, mode="baseline"))
        summary = asyncio.run(run_batch(RUN / "input.json", RUN, config))
        freeze()
        print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
