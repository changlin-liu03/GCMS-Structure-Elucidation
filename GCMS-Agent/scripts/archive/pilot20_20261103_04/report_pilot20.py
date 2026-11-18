"""Freeze then evaluate the fixed pilot, retaining failed cases and all costs.

No API calls. The freeze command does not open labels. Evaluation requires an
unchanged frozen inventory. Scientific results and original ledgers are not edited.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import sys

PROJECT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT / "src"))
from gcms_agent.config import digest, write_json
from gcms_agent.data.loader import load_inputs
from gcms_agent.workflow.runner import scientific_config_hash, source_versions
from gcms_agent.workflow.budget import DEFAULT_CNY, token_cost

TOKEN_FIELDS = ("input_tokens", "cache_read_input_tokens", "output_tokens")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def discover(root):
    root = Path(root).resolve()
    return [root] + sorted(p.parent for p in (root / "continuation/shards").glob("*/manifest.json"))


def inventory(root, baseline, inputs, selection):
    paths = {Path(inputs).resolve(), Path(selection).resolve()}
    directories = discover(root) + [Path(baseline).resolve()]
    for directory in directories:
        paths.update(directory / name for name in ("manifest.json", "logs/budget.json", "input.json", "attempt.json")
                     if (directory / name).exists())
        paths.update((directory / "results").glob("*.json"))
        paths.update((directory / "checkpoints").glob("*_failure.json"))
        paths.update((directory / "diagnostics").glob("*.json*"))
    return {str(p): sha(p) for p in sorted(paths)}


def collect(root, baseline, inputs, selection):
    rows = load_inputs(inputs)
    chosen = read(selection)
    expected = chosen["sample_ids"]
    if len(expected) != 20 or len(set(expected)) != 20:
        raise ValueError("This report requires exactly the fixed 20 distinct pilot IDs")
    if [r["sample_id"] for r in rows] != expected:
        raise ValueError("Pilot input order does not match the registered selection")
    main = read(Path(root) / "manifest.json")
    if main["sample_ids"] != expected or main["input_hash"] != digest(rows):
        raise ValueError("Original run input manifest differs from fixed pilot")
    if source_versions() != main["versions"]:
        raise ValueError("Current scientific code differs from the frozen experiment")
    by_id, origin, ledgers = {}, {}, []
    for directory in discover(root):
        manifest = read(directory / "manifest.json")
        if manifest["mode"] != "agent" or manifest["backend"] != "claude_agent_sdk":
            raise ValueError("Pilot requires recorded live Claude Agent SDK inference")
        if manifest["versions"] != main["versions"] or scientific_config_hash(manifest["config"]) != scientific_config_hash(main["config"]):
            raise ValueError("Scientific versions differ across pilot segments")
        if not set(manifest["sample_ids"]).issubset(expected):
            raise ValueError("Unexpected sample in a pilot segment")
        input_by_id = {row["sample_id"]: row for row in rows}
        if manifest["input_hash"] != digest([input_by_id[sid] for sid in manifest["sample_ids"]]):
            raise ValueError("Pilot segment input hash differs from the registered cases")
        for path in sorted((directory / "results").glob("*.json")):
            value = read(path)
            sid = value["sample_id"]
            if sid in by_id:
                raise ValueError("Duplicate case across pilot segments; refuse cherry-picking")
            if sid not in expected or sid not in manifest["sample_ids"]:
                raise ValueError("Unexpected result ID")
            original = next(r for r in rows if r["sample_id"] == sid)
            if value["input_hash"] != digest(original):
                raise ValueError("Result input hash mismatch")
            by_id[sid], origin[sid] = value, str(path.resolve())
        ledger = read(directory / "logs/budget.json")
        if ledger["currency"] != "CNY" or ledger.get("pending_reservations"):
            raise ValueError("Cannot freeze/evaluate an active or non-CNY pilot")
        ledgers.append((directory, ledger))
    base_root = Path(baseline).resolve()
    base_manifest = read(base_root / "manifest.json")
    if base_manifest["backend"] != "deterministic_baseline" or base_manifest["input_hash"] != digest(rows):
        raise ValueError("Baseline does not match pilot inputs")
    if base_manifest["versions"] != main["versions"]:
        raise ValueError("Baseline scientific versions differ from the pilot")
    baseline_rows = {v["sample_id"]: v for p in sorted((base_root / "results").glob("*.json")) for v in [read(p)]}
    if set(baseline_rows) != set(expected):
        raise ValueError("Baseline must cover all 20 cases")
    return rows, chosen, by_id, origin, ledgers, baseline_rows


def freeze(args):
    output = Path(args.output).resolve()
    if (output / "prediction_freeze.json").exists():
        raise ValueError("Freeze already exists; use a different report directory")
    rows, chosen, results, _, _, _ = collect(args.run, args.baseline, args.inputs, args.selection)
    recovered = sorted(set(args.externally_resumed_sample))
    if set(recovered) - set(chosen["sample_ids"]):
        raise ValueError("Recovery declaration contains an unexpected sample")
    record = {"frozen_utc": datetime.now(timezone.utc).isoformat(), "labels_read": False,
              "root": str(Path(args.run).resolve()), "baseline": str(Path(args.baseline).resolve()),
              "inputs": str(Path(args.inputs).resolve()), "selection": str(Path(args.selection).resolve()),
              "requested_sample_ids": chosen["sample_ids"], "result_sample_ids": sorted(results),
              "externally_resumed_samples": recovered,
              "first_pass_status_note": "Completed without external resume excludes explicitly declared resumed cases; original overwritten result snapshots are unavailable.",
              "report_script_sha256": sha(__file__),
              "files": inventory(args.run, args.baseline, args.inputs, args.selection)}
    write_json(output / "prediction_freeze.json", record)
    return {"frozen_cases": len(results), "requested_cases": len(rows), "labels_read": False,
            "freeze": str(output / "prediction_freeze.json")}


def proportion(n, d):
    return {"hits": n, "denominator": d, "fraction": n / d if d else None}


def distribution(values):
    if not values:
        return None
    values = sorted(values)
    return {"n": len(values), "mean": statistics.mean(values), "median": statistics.median(values),
            "p90_nearest_rank": values[max(0, (9 * len(values) + 9) // 10 - 1)], "max": max(values)}


def assess(rows, results, truth):
    from gcms_agent.evaluation.metrics import canonical
    from gcms_agent.chemistry.smiles_io import parse_smiles
    cases = []
    count = Counter()
    for row in rows:
        sid, target = row["sample_id"], truth[row["sample_id"]]
        result = results.get(sid, {})
        complete = bool(result.get("ranking_complete"))
        unique = result.get("unique_ranking", [])
        ranked_hit = next((i + 1 for i, c in enumerate(unique) if canonical(c.get("final_smiles")) == target), None)
        top = result.get("top1")
        actual_top_hit = bool(top and canonical(top.get("final_smiles")) == target)
        current = {"sample_id": sid, "has_result": bool(result), "complete": complete,
                   "has_top1": bool(top), "top1_correct": actual_top_hit,
                   "first_true_rank_in_unique_ranking": ranked_hit,
                   "strict_top1_hit": complete and actual_top_hit,
                   "strict_top10_hit": complete and ranked_hit is not None and ranked_hit <= 10,
                   "final_top1": top.get("final_smiles") if top else None,
                   "top1_score": top.get("score", {}).get("score") if top else None,
                   "top1_supported_peak_count": len(top.get("score", {}).get("supported_peaks", [])) if top else 0,
                   "top1_quantization_sensitive": top.get("quantization_sensitive") if top else None,
                   "stop_reasons": dict(Counter(c.get("stop_reason", "missing") for c in result.get("ranked_candidates", [])))}
        candidates = {c["candidate_id"]: c for c in result.get("ranked_candidates", [])}
        if len(candidates) != len(result.get("ranked_candidates", [])):
            raise ValueError("Duplicate lineage ID in result")
        unique_candidates = {}
        for i, original in enumerate(row["generated"]):
            c = candidates.get(f"c{i + 1:02d}", {})
            before_valid = bool(parse_smiles(original)["valid"])
            final = c.get("final_smiles")
            final_valid = bool(final and parse_smiles(final)["valid"])
            count["expected_slots"] += 1
            count["returned_slots"] += bool(c)
            count["emitted_smiles"] += final is not None
            count["strict_valid_smiles"] += final_valid
            count["initial_strict_valid_slots"] += before_valid
            count["initial_invalid_slots"] += not before_valid
            count["repaired_slots"] += not before_valid and final_valid
            correct_before = canonical(original) == target
            count["original_correct_slots"] += correct_before
            count["damaged_correct_slots"] += correct_before and canonical(final) != target
            if c:
                unique_candidates[c.get("computed_candidate_id", c["candidate_id"])] = c
        current["strict_valid_outputs"] = sum(bool(c.get("final_smiles") and parse_smiles(c["final_smiles"])["valid"]) for c in candidates.values())
        current["unique_source_jobs"] = len(unique_candidates)
        current["accepted_edits_unique_jobs"] = sum(c.get("accepted_edits", 0) for c in unique_candidates.values())
        cases.append(current)
    n = len(rows)
    completed = sum(c["complete"] for c in cases)
    strict1, strict10 = sum(c["strict_top1_hit"] for c in cases), sum(c["strict_top10_hit"] for c in cases)
    summary = {"requested_cases": n, "result_cases": sum(c["has_result"] for c in cases),
               "complete_cases": completed, "complete_with_top1": sum(c["complete"] and c["has_top1"] for c in cases),
               "strict_end_to_end_acc_at_1": proportion(strict1, n), "strict_end_to_end_acc_at_10": proportion(strict10, n),
               "complete_only_acc_at_1": proportion(strict1, completed), "complete_only_acc_at_10": proportion(strict10, completed),
               "provisional_incomplete_top1_hits": sum(c["has_result"] and not c["complete"] and c["top1_correct"] for c in cases),
               "provisional_incomplete_top10_hits": sum(c["has_result"] and not c["complete"] and c["first_true_rank_in_unique_ranking"] is not None and c["first_true_rank_in_unique_ranking"] <= 10 for c in cases),
               "ranking_array_hits_regardless_completion_or_top1_abstention": {
                   "at_1": sum(c["first_true_rank_in_unique_ranking"] == 1 for c in cases),
                   "at_10": sum(c["first_true_rank_in_unique_ranking"] is not None and c["first_true_rank_in_unique_ranking"] <= 10 for c in cases)},
               "candidate_counts": dict(count),
               "valid_output_fraction_of_200_slots": proportion(count["strict_valid_smiles"], count["expected_slots"]),
               "emitted_smiles_validity": proportion(count["strict_valid_smiles"], count["emitted_smiles"]),
               "repair_success_rate": proportion(count["repaired_slots"], count["initial_invalid_slots"]),
               "tie_policy": "Canonical SMILES display order; no optimistic tie credit"}
    return summary, cases


def costs(ledgers, selection, results):
    usage = {f: 0 for f in TOKEN_FIELDS}
    by_case = defaultdict(lambda: {"cost_cny": 0, "sessions": 0, "unknown_usage_sessions": 0,
                                   "session_elapsed_seconds": 0, **{f: 0 for f in TOKEN_FIELDS}})
    failure_receipts, total, unknown, sessions = [], 0.0, 0, 0
    for directory, ledger in ledgers:
        total += ledger["spent_cny"]
        for request in ledger.get("requests", []):
            sid = request.get("sample_id")
            if sid not in selection["sample_ids"]:
                raise ValueError("Unattributed or unexpected request costs")
            item = by_case[sid]
            item["cost_cny"] += request["cost_cny"]
            item["sessions"] += 1
            sessions += 1
            if request.get("started_utc") and request.get("settled_utc"):
                item["session_elapsed_seconds"] += (datetime.fromisoformat(request["settled_utc"]) - datetime.fromisoformat(request["started_utc"])).total_seconds()
            known = request.get("usage", {}).get("known", False)
            item["unknown_usage_sessions"] += not known
            unknown += not known
            if known:
                for field in TOKEN_FIELDS:
                    value = request["usage"].get(field, 0)
                    usage[field] += value
                    item[field] += value
        for path in sorted((directory / "checkpoints").glob("*_failure.json")):
            record = read(path)
            failure_receipts.append({"path": str(path.resolve()), "category": record.get("error_category"),
                                     "sample_id": path.name.split("__")[0],
                                     "usage_known": record.get("usage", {}).get("known", False),
                                     "session_metrics": record.get("audit", {}).get("session_metrics", {})})
    if abs(total - sum(c["cost_cny"] for c in by_case.values())) > 1e-7:
        raise ValueError("Ledger spend differs from sum of request receipts")
    blockers = []
    if set(results) != set(selection["sample_ids"]) or not all(r.get("ranking_complete") for r in results.values()):
        blockers.append("one_or_more_cases_incomplete_or_unprocessed")
    if unknown:
        blockers.append("one_or_more_sessions_have_unknown_usage")
    if any(sum(by_case[sid][f] for f in TOKEN_FIELDS) <= 0 for sid in selection["sample_ids"]):
        blockers.append("one_or_more_cases_have_no_positive_known_usage")
    projection = None
    if not blockers:
        strata = defaultdict(list)
        for item in selection["profiles"]:
            strata[item["stratum"]].append(by_case[item["sample_id"]])
        projection = {f"{tariff}_cny": selection["population"] * sum(
            selection["strata"][key]["weight"] * statistics.mean(token_cost(u, prices) for u in group)
            for key, group in strata.items()) for tariff, prices in DEFAULT_CNY.items()}
    summary = {"accounted_cost_cny": total, "sessions": sessions, "usage": usage,
               "unknown_usage_sessions": unknown, "known_usage_offpeak_equivalent_cny": token_cost(usage, DEFAULT_CNY["offpeak"]),
               "known_usage_peak_equivalent_cny": token_cost(usage, DEFAULT_CNY["peak"]),
               "case_cost_cny": distribution([c["cost_cny"] for c in by_case.values()]),
               "case_session_elapsed_seconds": distribution([c["session_elapsed_seconds"] for c in by_case.values()]),
               "case_sessions_exceeding_output_target": [sid for sid, c in by_case.items() if c["output_tokens"] > 25000],
               "projection_population": selection["population"], "projection": projection,
               "projection_blockers": blockers,
               "projection_policy": "Population-weighted stratum means only for a fully complete known-usage pilot; not invoice or accuracy guarantee",
               "failure_receipt_categories": dict(Counter(r["category"] for r in failure_receipts)),
               "failure_receipts": failure_receipts}
    return summary, dict(by_case)


def evaluate(args):
    output = Path(args.output).resolve()
    frozen = read(output / "prediction_freeze.json")
    if frozen["report_script_sha256"] != sha(__file__):
        raise ValueError("Report script changed after prediction freeze")
    current = inventory(frozen["root"], frozen["baseline"], frozen["inputs"], frozen["selection"])
    if current != frozen["files"]:
        raise ValueError("Predictions/ledgers changed since freeze; refuse label evaluation")
    rows, selection, results, origin, ledgers, base_rows = collect(frozen["root"], frozen["baseline"], frozen["inputs"], frozen["selection"])
    # Labels are first opened only after every frozen-input check above passes.
    from gcms_agent.evaluation.metrics import canonical
    from gcms_agent.evaluation.baselines import original_order_metrics
    labels = read(args.labels)
    if len({r["sample_id"] for r in labels}) != len(labels):
        raise ValueError("Duplicate label IDs")
    truth = {r["sample_id"]: canonical(r["true_smiles"]) for r in labels if r["sample_id"] in selection["sample_ids"]}
    if set(truth) != set(selection["sample_ids"]) or not all(truth.values()):
        raise ValueError("Missing or invalid pilot labels")
    agent, cases = assess(rows, results, truth)
    baseline, baseline_cases = assess(rows, base_rows, truth)
    cost_report, by_case = costs(ledgers, selection, results)
    for case in cases:
        sid = case["sample_id"]
        case.update(source_result=origin.get(sid), resource_usage=by_case.get(sid),
                    externally_resumed=sid in frozen["externally_resumed_samples"])
    agent["complete_without_external_resume"] = sum(c["complete"] and not c["externally_resumed"] for c in cases)
    agent["first_pass_completion_rate"] = proportion(agent["complete_without_external_resume"], len(rows))
    agent["completion_rate_including_external_resume"] = proportion(agent["complete_cases"], len(rows))
    report = {"created_utc": datetime.now(timezone.utc).isoformat(), "prediction_freeze": str(output / "prediction_freeze.json"),
              "evaluation_labels_sha256": sha(args.labels), "labels_used_only_after_freeze": True,
              "agent": agent, "deterministic_baseline": baseline,
              "original_diffusion_order": original_order_metrics(rows, labels), "costs": cost_report,
              "notes": ["Strict end-to-end metrics count incomplete and unprocessed cases as misses.",
                        "A null top1 is abstention and cannot be an Acc@1 hit even if unique_ranking[0] matches.",
                        "All paid attempts, including failures and external recovery, remain in costs.",
                        "Completed without external resume is not a reconstruction of overwritten first-pass result snapshots.",
                        "Structural support score is not a calibrated identity probability."]}
    write_json(output / "report.json", report)
    write_json(output / "cases.json", cases)
    write_json(output / "baseline_cases.json", baseline_cases)
    return {"report": str(output / "report.json"), "cases": len(cases),
            "complete": agent["complete_cases"], "strict_acc1": agent["strict_end_to_end_acc_at_1"],
            "strict_acc10": agent["strict_end_to_end_acc_at_10"], "cost_cny": cost_report["accounted_cost_cny"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    freeze_parser = sub.add_parser("freeze")
    freeze_parser.add_argument("--run", required=True)
    freeze_parser.add_argument("--baseline", required=True)
    freeze_parser.add_argument("--inputs", default=str(PROJECT / "data/inference/economy_pilot.json"))
    freeze_parser.add_argument("--selection", default=str(PROJECT / "data/inference/economy_pilot.manifest.json"))
    freeze_parser.add_argument("--externally-resumed-sample", action="append", default=[])
    freeze_parser.add_argument("--output", required=True)
    evaluate_parser = sub.add_parser("evaluate")
    evaluate_parser.add_argument("--output", required=True)
    evaluate_parser.add_argument("--labels", required=True)
    args = parser.parse_args()
    print(json.dumps(freeze(args) if args.command == "freeze" else evaluate(args), ensure_ascii=False))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
