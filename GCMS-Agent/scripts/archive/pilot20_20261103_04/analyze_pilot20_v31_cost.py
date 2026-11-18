"""Read-only, label-free session cost analysis for the frozen v31 pilot.

Run only after inference ends. Request receipts are the sole unit of accounting;
candidate lineage copies never contribute additional cost. Round attribution uses
checkpoint history references, not batch sequence numbers. No model text or hidden
reasoning is exported, and output tokens cannot be split into thought/final tokens.
"""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics

PROJECT = Path(__file__).resolve().parents[3]
FIELDS = ("input_tokens", "cache_read_input_tokens", "output_tokens")
PRICE_FIELDS = dict(zip(FIELDS, ("input", "cache", "output")))
COMPONENTS = ("input_cny", "cache_cny", "output_cny", "unallocated_estimated_cny")
TOLERANCE = 1e-7


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"Invalid nonnegative finite {name}")
    return value


def close(left, right, message):
    if abs(left - right) > TOLERANCE:
        raise ValueError(f"{message}: {left} != {right}")


def distribution(values):
    values = sorted(values)
    if not values:
        return None
    return {"n": len(values), "mean": statistics.mean(values), "median": statistics.median(values),
            "p90_nearest_rank": values[math.ceil(len(values) * .9) - 1], "max": values[-1]}


def empty_group():
    return {"sessions": 0, "unknown_usage_sessions": 0, "cost_cny": 0.0,
            "session_elapsed_seconds": 0.0, **dict.fromkeys(FIELDS, 0), **dict.fromkeys(COMPONENTS, 0.0)}


def add(group, request):
    group["sessions"] += 1
    group["unknown_usage_sessions"] += not request["usage_known"]
    for name in ("cost_cny", "session_elapsed_seconds", *FIELDS, *COMPONENTS):
        group[name] += request[name]


def token_cost(usage, prices):
    return sum(usage.get(field, 0) * prices[price] for field, price in PRICE_FIELDS.items()) / 1e6


def account(ledger):
    """Validate a single settled ledger and return one record per paid session."""
    if ledger.get("currency") != "CNY" or ledger.get("pending_reservations"):
        raise ValueError("Cost analysis requires a settled CNY ledger with no pending reservations")
    ids, records = set(), []
    tariffs = ledger["prices_cny"]
    for raw in ledger["requests"]:
        rid = raw["request_id"]
        if rid in ids:
            raise ValueError(f"Duplicate paid request ID: {rid}")
        ids.add(rid)
        usage = raw.get("usage", {})
        known = usage.get("known") is True
        record = {"request_id": rid, "sample_id": raw.get("sample_id"),
                  "candidate_ids": raw.get("candidate_ids", []), "usage_known": known,
                  "cost_cny": number(raw["cost_cny"], "receipt cost"),
                  "charged_tariff": raw.get("charged_tariff"),
                  "session_elapsed_seconds": 0.0,
                  **dict.fromkeys(FIELDS, 0), **dict.fromkeys(COMPONENTS, 0.0)}
        if raw.get("started_utc") and raw.get("settled_utc"):
            record["session_elapsed_seconds"] = number((datetime.fromisoformat(raw["settled_utc"])
                - datetime.fromisoformat(raw["started_utc"])).total_seconds(), "session duration")
        if known:
            if record["charged_tariff"] not in tariffs:
                raise ValueError(f"Known usage has no recorded tariff: {rid}")
            prices = tariffs[record["charged_tariff"]]
            for field, price in PRICE_FIELDS.items():
                value = number(usage.get(field, 0), field)
                if int(value) != value:
                    raise ValueError(f"Non-integer token count: {rid}/{field}")
                record[field] = value
                record[f"{price}_cny"] = value * number(prices[price], "tariff") / 1e6
        else:
            # Interrupted/unknown sessions are charged by the saved reservation;
            # do not fabricate a split between token categories.
            record["unallocated_estimated_cny"] = record["cost_cny"]
        close(record["cost_cny"], sum(record[field] for field in COMPONENTS), f"Receipt token reconciliation {rid}")
        records.append(record)
    total = empty_group()
    for record in records:
        add(total, record)
    close(number(ledger["spent_cny"], "ledger spend"), total["cost_cny"], "Ledger/request reconciliation")
    if ledger.get("sessions", len(records)) != len(records):
        raise ValueError("Ledger session total differs from request count")
    if ledger.get("unknown_usage_sessions", total["unknown_usage_sessions"]) != total["unknown_usage_sessions"]:
        raise ValueError("Ledger unknown-usage count differs from requests")
    for field in FIELDS:
        close(ledger.get("usage", {}).get(field, total[field]), total[field], f"Ledger token reconciliation {field}")
    return records, total


def receipt_metadata(run):
    """Join accounting to structural receipt/history metadata, without text export."""
    root = run / "checkpoints"
    round_refs = defaultdict(set)
    for path in sorted(root.glob("*__case.json")):
        envelope = read(path)
        for state in envelope.get("states", {}).values():
            for item in state.get("history", []):
                round_number = item.get("round")
                if not isinstance(round_number, int) or round_number < 1:
                    continue
                for name in ("review_receipt", "failure_receipt"):
                    if item.get(name):
                        round_refs[item[name]].add(round_number)
    metadata = {}
    for path in sorted(root.glob("*_review.json")):
        receipt = read(path)
        rid = receipt["request_id"]
        if rid in metadata:
            raise ValueError(f"Duplicate success receipt for {rid}")
        metrics = receipt.get("review", {}).get("audit", {}).get("session_metrics", {})
        retained = metrics.get("terminal_reason", "") in ("turn_limit_after_submission", "output_limit_after_submission")
        metadata[rid] = {"session_outcome": "submitted_after_resource_limit" if retained else "submitted",
                         "terminal_reason": metrics.get("terminal_reason"),
                         "failure_category": None,
                         "rounds_recorded": sorted(round_refs[path.stem]),
                         "receipt": str(path.relative_to(run))}
    for path in sorted(root.glob("*_failure.json")):
        receipt = read(path)
        rid = receipt["request_id"]
        if rid in metadata:
            raise ValueError(f"Conflicting success/failure receipts for {rid}")
        metadata[rid] = {"session_outcome": "failed",
                         "failure_category": receipt.get("error_category", "unknown"),
                         "terminal_reason": None,
                         "rounds_recorded": sorted(round_refs[path.stem]),
                         "receipt": str(path.relative_to(run))}
    for value in metadata.values():
        rounds = value["rounds_recorded"]
        value["round"] = rounds[0] if len(rounds) == 1 else None
        value["round_attribution"] = "checkpoint_history" if len(rounds) == 1 else "unavailable_or_ambiguous"
    return metadata


def project_population(selection, cases, results, ledger, total):
    expected = selection["sample_ids"]
    blockers = []
    if set(results) != set(expected) or not all(results[sid].get("ranking_complete") for sid in expected if sid in results):
        blockers.append("one_or_more_cases_incomplete_or_unprocessed")
    if total["unknown_usage_sessions"]:
        blockers.append("unknown_usage")
    if ledger.get("reservation_exceeded"):
        blockers.append("reservation_exceeded")
    if any(not sum(cases[sid][field] for field in FIELDS) for sid in expected):
        blockers.append("one_or_more_cases_without_positive_known_usage")
    groups = defaultdict(list)
    for profile in selection["profiles"]:
        groups[profile["stratum"]].append(cases[profile["sample_id"]])
    strata = {}
    for name, stratum in selection["strata"].items():
        group = groups[name]
        if len(group) != stratum["selected"] or not group:
            raise ValueError("Stratum allocation differs from selection manifest")
        strata[name] = {"population": stratum["population"], "selected": len(group),
                        "weight": stratum["weight"],
                        "observed_mean_cost_cny": statistics.mean(c["cost_cny"] for c in group),
                        "cost_cny": distribution([c["cost_cny"] for c in group]),
                        "complete_cases": sum(bool(results.get(c["sample_id"], {}).get("ranking_complete")) for c in group)}
    close(sum(s["weight"] for s in strata.values()), 1.0, "Population weights")
    if sum(s["population"] for s in strata.values()) != selection["population"]:
        raise ValueError("Population count differs across strata")
    projected = None
    if not blockers:
        projected = {}
        for name, prices in ledger["prices_cny"].items():
            cost = sum(strata[key]["population"] * statistics.mean(token_cost(c, prices) for c in group)
                       for key, group in groups.items())
            projected[name] = {"population_cost_cny": cost, "per_case_cny": cost / selection["population"],
                               "within_200_cny": cost <= 200, "within_300_cny": cost <= 300}
    return {"population": selection["population"], "strata": strata, "projection": projected,
            "projection_blockers": blockers,
            "budget_per_case_cny": {"for_200": 200 / selection["population"], "for_300": 300 / selection["population"]},
            "policy": "Population count times sampled stratum mean, including failed sessions and retries; only if every requested case is complete and usage is known.",
            "limitations": "Previously inspected development regression; only 1-5 cases per stratum. Not a reliable cost bound, billing invoice, statistical interval, or unseen accuracy estimate. Tariff scenarios use the run's frozen prices."}


def analyze(args):
    run = Path(args.run).resolve()
    if not (run / "summary.json").exists():
        raise ValueError("Run has no final summary; wait for inference to end")
    manifest, summary = read(run / "manifest.json"), read(run / "summary.json")
    selection = read(args.selection)
    expected = selection["sample_ids"]
    if len(expected) != 20 or len(set(expected)) != 20 or manifest["sample_ids"] != expected:
        raise ValueError("This analyzer requires the exact original ordered 20 cases")
    profile_ids = [profile["sample_id"] for profile in selection["profiles"]]
    if len(profile_ids) != 20 or set(profile_ids) != set(expected):
        raise ValueError("Stratum profiles must cover the same 20 cases exactly once")
    if manifest["mode"] != "agent" or manifest["backend"] != "claude_agent_sdk":
        raise ValueError("Expected real Claude Agent SDK inference")
    if list((run / "continuation/shards").glob("*/logs/budget.json")):
        raise ValueError("Independent v31 run unexpectedly has shards; explicit multi-ledger accounting required")
    ledger = read(run / "logs/budget.json")
    requests, total = account(ledger)
    results = {value["sample_id"]: value for path in sorted((run / "results").glob("*.json")) for value in [read(path)]}
    if not set(results).issubset(expected) or len(results) != summary["processed_samples"]:
        raise ValueError("Final summary/result case count mismatch")
    metadata = receipt_metadata(run)
    cases = {sid: {"sample_id": sid, **empty_group(), "ranking_complete": bool(results.get(sid, {}).get("ranking_complete")),
                   "has_result": sid in results} for sid in expected}
    by_round, by_outcome, by_failure, by_tariff = (defaultdict(empty_group) for _ in range(4))
    for request in requests:
        sid, rid = request["sample_id"], request["request_id"]
        if sid not in cases:
            raise ValueError(f"Unexpected or unattributed paid request: {rid}")
        request.update(metadata.get(rid, {"session_outcome": "unclassified_no_receipt", "round": None,
            "rounds_recorded": [], "round_attribution": "unavailable_or_ambiguous", "failure_category": None}))
        add(cases[sid], request)
        add(by_round[str(request["round"]) if request["round"] is not None else "unknown"], request)
        add(by_outcome[request["session_outcome"]], request)
        add(by_tariff[request["charged_tariff"] or "unknown"], request)
        if request["session_outcome"] == "failed":
            add(by_failure[request["failure_category"]], request)
    # Resource usage is a second reconciliation source, not another billing unit.
    for sid, result in results.items():
        usage = result.get("resource_usage", {})
        for key in ("sessions", "cost_cny", *FIELDS):
            if key in usage:
                close(usage[key], cases[sid][key], f"Case/ledger reconciliation {sid}/{key}")
    for name, groups in (("case", cases), ("round", by_round), ("outcome", by_outcome), ("tariff", by_tariff)):
        close(sum(group["cost_cny"] for group in groups.values()), total["cost_cny"], f"{name} aggregation")
    close(summary["spent_cny"], total["cost_cny"], "Summary/ledger reconciliation")
    old = read(args.old_report)
    prior = old["costs"]
    completed = sum(case["ranking_complete"] for case in cases.values())
    if summary["requested_samples"] != len(expected) or summary["complete_samples"] != completed:
        raise ValueError("Final summary completion count mismatch")
    cost_distribution = distribution([case["cost_cny"] for case in cases.values()])
    comparison = {"old_report": str(Path(args.old_report).resolve()),
        "old": {"cost_cny": prior["accounted_cost_cny"], "sessions": prior["sessions"],
                "complete_cases": old["agent"]["complete_cases"], "requested_cases": old["agent"]["requested_cases"],
                "usage": prior["usage"], "case_cost_cny": prior["case_cost_cny"]},
        "new": {"cost_cny": total["cost_cny"], "sessions": total["sessions"], "complete_cases": completed,
                "requested_cases": len(expected), "usage": {f: total[f] for f in FIELDS}, "case_cost_cny": cost_distribution},
        "cost_delta_cny": total["cost_cny"] - prior["accounted_cost_cny"],
        "cost_ratio": total["cost_cny"] / prior["accounted_cost_cny"] if prior["accounted_cost_cny"] else None,
        "note": "Lower spend on incomplete runs does not demonstrate efficiency; both totals retain failures/retries. No identification metrics are calculated here."}
    output = {"created_utc": datetime.now(timezone.utc).isoformat(), "run": str(run),
        "script_sha256": sha(__file__), "manifest_sha256": sha(run / "manifest.json"),
        "ledger_sha256": sha(run / "logs/budget.json"), "labels_read": False,
        "unit": "one unique ledger request_id per SDK session; never per candidate lineage",
        "round_policy": "Recorded candidate history receipt references; batch sequence is not a round. Ambiguous/missing history stays unknown.",
        "session_outcome_policy": "Submitted means a review receipt was returned, not molecular correctness or workflow completion. Retained submissions after resource limits are separate. Failure receipt means failed session; retries remain separately counted.",
        "output_token_policy": "All billed output tokens together. No inferred split between reasoning, tool arguments, and final prose.",
        "requested_cases": len(expected), "processed_cases": len(results), "complete_cases": completed,
        "totals": total, "by_case": cases, "by_round": dict(by_round), "by_session_outcome": dict(by_outcome),
        "by_failure_category": dict(by_failure), "by_charged_tariff": dict(by_tariff), "sessions": requests,
        "case_cost_cny": cost_distribution,
        "case_session_elapsed_seconds": distribution([case["session_elapsed_seconds"] for case in cases.values()]),
        "cases_over_25000_output_tokens": [sid for sid, case in cases.items() if case["output_tokens"] > 25000],
        "budget": {"limit_cny": ledger["limit_cny"], "spent_cny": total["cost_cny"],
                   "remaining_cny": ledger["limit_cny"] - total["cost_cny"], "pending_reservations": ledger["pending_reservations"],
                   "reservation_exceeded": ledger.get("reservation_exceeded", False)},
        "reconciliation": {"passed": True, "tolerance_cny": TOLERANCE, "duplicate_request_ids": 0,
                           "unclassified_sessions": by_outcome.get("unclassified_no_receipt", {}).get("sessions", 0),
                           "round_unknown_sessions": by_round.get("unknown", {}).get("sessions", 0)},
        "old_vs_new": comparison, "population_cost": project_population(selection, cases, results, ledger, total)}
    path = Path(args.output).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"output": str(path), "complete_cases": completed, "cost_cny": total["cost_cny"],
            "sessions": total["sessions"], "reconciliation": output["reconciliation"],
            "projection": output["population_cost"]["projection"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", default=str(PROJECT / "runs/pilot20_v31_20261104"))
    parser.add_argument("--selection", default=str(PROJECT / "data/inference/economy_pilot.manifest.json"))
    parser.add_argument("--old-report", default=str(PROJECT / "runs/economy_pilot20_report_20261104/report.json"))
    parser.add_argument("--output", default=str(PROJECT / "runs/pilot20_v31_report_20261104/cost_breakdown.json"))
    parser.add_argument("--check-ledger", help="Validate a settled historical ledger's arithmetic only; writes no files")
    args = parser.parse_args()
    if args.check_ledger:
        records, total = account(read(args.check_ledger))
        result = {"arithmetic_check_passed": True, "file": str(Path(args.check_ledger).resolve()),
                  "request_count": len(records), "totals": total}
    else:
        result = analyze(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
