"""No-label, no-API quality audit of the sealed v31 development pilot.

Reuses the frozen chemistry/rule validators; never treats these checks as
experimental confirmation or interprets unverified model prose.
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
from gcms_agent.chemistry.smiles_io import parse_smiles
from gcms_agent.chemistry.validation import validate_graph
from gcms_agent.config import write_json
from gcms_agent.fragmentation.enumeration import analyze_spectrum
from gcms_agent.reporting.quality import explanation_quality
from gcms_agent.scoring.support import calculate_support
from gcms_agent.workflow.runner import source_versions

DEFAULT_RUN = PROJECT / "runs/pilot20_v31_20261104"
DEFAULT_OUTPUT = PROJECT / "runs/pilot20_v31_report_20261104/quality_audit.json"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def event_key(event):
    return json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def distribution(values):
    if not values:
        return None
    ordered = sorted(values)
    return {"n": len(values), "min": ordered[0], "median": statistics.median(values),
            "mean": statistics.mean(values), "max": ordered[-1]}


def audit_case(result):
    """Audit a result in memory; every structural lineage remains a slot."""
    candidates = result["ranked_candidates"]
    unique = {c.get("computed_candidate_id", c["candidate_id"]): c for c in candidates}
    slot_counts = Counter(total=len(candidates))
    invalid_outputs, graph_failures = [], []
    parsed_cache = {}
    for c in candidates:
        initial_valid = bool(c.get("initial_validation", {}).get("valid"))
        slot_counts["initial_valid" if initial_valid else "initial_invalid"] += 1
        final = c.get("final_smiles")
        if final is None:
            slot_counts["null_final_smiles"] += 1
            continue
        slot_counts["emitted_smiles"] += 1
        if final not in parsed_cache:
            parsed = parse_smiles(final)
            parsed_cache[final] = bool(parsed.get("valid"))
        valid = parsed_cache[final]
        slot_counts["strict_valid_smiles" if valid else "invalid_emitted_smiles"] += 1
        if valid and not initial_valid:
            slot_counts["repaired_initial_invalid"] += 1
        if not valid:
            invalid_outputs.append(c["candidate_id"])
    for cid, c in unique.items():
        if c.get("final_smiles") is None:
            continue
        checked = validate_graph(c["graph"], c.get("composition"))
        if not checked["valid"] or checked["canonical_smiles"] != c["final_smiles"]:
            graph_failures.append({"candidate_id": cid, "errors": checked["errors"],
                                   "matches_final_smiles": checked["canonical_smiles"] == c["final_smiles"]})

    proposal_counts, rejection_reasons, committed_operations = Counter(), Counter(), []
    committed_records = []
    for cid, c in unique.items():
        proposal_counts["stored_accepted_edits"] += c.get("accepted_edits", 0)
        for history in c.get("history", []):
            for outcome in history.get("outcomes", []):
                proposal_counts["submitted_outcomes"] += 1
                verdict = outcome.get("verdict", {})
                proposal_counts["host_accepted_outcomes"] += bool(verdict.get("accepted"))
                if not verdict.get("accepted"):
                    rejection_reasons[verdict.get("reason", "unspecified")] += 1
                if outcome.get("committed"):
                    count = len(outcome.get("proposal", {}).get("operations", []))
                    committed_operations.append(count)
                    proposal_counts["committed_transactions"] += 1
                    proposal_counts["committed_basic_operations"] += count
                    committed_records.append({"candidate_id": cid, "round": history["round"],
                                              "basic_operations": count,
                                              "acceptance_reason": verdict.get("reason")})

    quality = result.get("explanation_quality", {})
    top, top_audit = result.get("top1"), None
    if top:
        selected = next(c for c in candidates if c.get("final_smiles") == top["final_smiles"])
        regenerated = analyze_spectrum(selected["graph"], result["peaks"])
        recorded_events = selected.get("evidence", {}).get("evidence", [])
        fresh_events = regenerated["evidence"]
        recorded_keys = Counter(event_key(e) for e in recorded_events)
        fresh_keys = Counter(event_key(e) for e in fresh_events)
        recalculated = calculate_support(result["peaks"], regenerated["supported_peaks"]) if regenerated["complete"] else None
        recorded_support = selected.get("evidence", {}).get("supported_peaks", [])
        top_audit = {
            "final_smiles": top["final_smiles"],
            "recorded_paths": len(recorded_events),
            "recorded_bond_cleavage_paths": sum(bool(e.get("broken_bonds")) for e in recorded_events),
            "regenerated_paths": len(fresh_events), "regeneration_complete": regenerated["complete"],
            "exact_path_multiset_match": recorded_keys == fresh_keys,
            "recorded_paths_missing_from_regeneration": sum((recorded_keys - fresh_keys).values()),
            "regenerated_paths_missing_from_record": sum((fresh_keys - recorded_keys).values()),
            "supported_peaks_match": recorded_support == regenerated["supported_peaks"],
            "score_recomputed_exactly": recalculated == top["score"],
            "verification_flags_all_true": all(
                e.get("verified") is True and all(e.get("verification", {}).get(k) is True
                    for k in ("atom_accounting", "charge", "electron_parity", "template_applicability"))
                for e in fresh_events),
            "positive_verified_path_count": len(fresh_events),
            "rule_counts": dict(Counter(e["rule_id"] for e in fresh_events)),
            "scope": "Same frozen rule engine replays applicability, atom/charge bookkeeping, electron parity and nominal-mass matching; not an independent chemical model or experiment",
            "identity_confirmed": False, "mechanism_experimentally_confirmed": False,
        }
    profile = quality.get("evidence_profile", {})
    formal_passed = None if top_audit is None else all(top_audit[key] for key in (
        "regeneration_complete", "exact_path_multiset_match", "supported_peaks_match",
        "score_recomputed_exactly", "verification_flags_all_true"))
    return {
        "sample_id": result["sample_id"], "workflow_complete": bool(result.get("ranking_complete")),
        "has_top1": top is not None, "candidate_slots": dict(slot_counts),
        "unique_computed_candidates": len(unique),
        "stop_reasons_slots": dict(Counter(c["stop_reason"] for c in candidates)),
        "stop_reasons_unique_candidates": dict(Counter(c["stop_reason"] for c in unique.values())),
        "quality_flags": quality.get("limitations", []),
        "quality_metadata_recomputed_exactly": quality == explanation_quality(result),
        "top1_tied": bool(top and top.get("tied")),
        "top1_score_intervals_overlap": bool(top and top.get("quantization_sensitive")),
        "top_score_candidate_count": result.get("ranking_diagnostics", {}).get("top_score_candidate_count", 0),
        "same_supported_peak_set_competitors": result.get("ranking_diagnostics", {}).get("same_supported_peak_set_competitors", []),
        "molecular_ion_only": bool(profile.get("molecular_ion_only")),
        "no_rule_supported_bond_cleavage": top is not None and not profile.get("bond_cleavage_peaks"),
        "point_coverage_below_50_percent": bool(profile.get("majority_intensity_unassigned_at_point_estimate")),
        "upper_coverage_below_50_percent": bool(profile.get("majority_intensity_unassigned_across_quantization_bounds")),
        "point_coverage": profile.get("rule_supported_intensity_fraction"),
        "invalid_emitted_candidate_ids": invalid_outputs, "final_graph_validation_failures": graph_failures,
        "submitted_proposals": dict(proposal_counts), "submitted_rejection_reasons": dict(rejection_reasons),
        "committed_basic_operation_counts": committed_operations, "committed_transactions": committed_records,
        "committed_count_matches_saved_counter": proposal_counts["committed_transactions"] == proposal_counts["stored_accepted_edits"],
        "top1_formal_revalidation_passed": formal_passed,
        "top1_formal_revalidation": top_audit,
    }


def audit_sessions(run, ledger):
    """One receipt per ledger request, never per candidate lineage."""
    receipts = defaultdict(list)
    for path in sorted((run / "checkpoints").glob("*.json")):
        if not (path.name.endswith("_review.json") or path.name.endswith("_failure.json")):
            continue
        record = read(path)
        if record.get("request_id"):
            receipts[record["request_id"]].append((path, record))
    rows = []
    seen = set()
    for request in ledger.get("requests", []):
        rid = request["request_id"]
        if rid in seen:
            raise ValueError("Duplicate ledger request ID")
        seen.add(rid)
        records = receipts.get(rid, [])
        if len(records) != 1:
            rows.append({"request_id": rid, "sample_id": request.get("sample_id"),
                         "receipt_count": len(records), "audit_available": False})
            continue
        path, record = records[0]
        review = record.get("review")
        audit = (review or record).get("audit", {})
        attempts = audit.get("submission_attempts", [])
        trials = audit.get("trials", [])
        rows.append({
            "request_id": rid, "sample_id": request.get("sample_id"), "receipt": path.name,
            "receipt_count": 1, "audit_available": True, "validated_submission": review is not None,
            "failure_category": record.get("error_category"),
            "terminal_reason": audit.get("session_metrics", {}).get("terminal_reason"),
            "format_errors": audit.get("format_errors", 0),
            "counted_format_error_events": sum(bool(a.get("counted_format_error")) for a in attempts),
            "submission_trace_records": len(attempts),
            "submission_trace_total": audit.get("submission_attempt_count", len(attempts)),
            "preflight_rejection_count": len(audit.get("preflight_errors", {})),
            "counted_error_reasons": dict(Counter(a.get("reason", "unspecified") for a in attempts
                                                  if a.get("counted_format_error"))),
            "trial_records": len(trials),
            "qualifying_trials": sum(bool((t.get("verdict") or {}).get("accepted")) for t in trials),
            "selected_trials": sum(bool(t.get("selected")) for t in trials),
        })
    return rows


def summarize(cases, sessions):
    flags = ("has_top1", "top1_tied", "top1_score_intervals_overlap", "molecular_ion_only",
             "no_rule_supported_bond_cleavage", "point_coverage_below_50_percent",
             "upper_coverage_below_50_percent")
    result = {"cases": len(cases), **{f: sum(bool(c[f]) for c in cases) for f in flags},
              "cases_with_same_supported_peak_set_competitors": sum(bool(c["same_supported_peak_set_competitors"]) for c in cases),
              "coverage_distribution": distribution([c["point_coverage"] for c in cases if c["point_coverage"] is not None])}
    for key in ("candidate_slots", "stop_reasons_slots", "stop_reasons_unique_candidates", "submitted_proposals", "submitted_rejection_reasons"):
        counts = Counter()
        for c in cases:
            counts.update(c[key])
        result[key] = dict(counts)
    result["quality_flag_counts"] = dict(Counter(f for c in cases for f in c["quality_flags"]))
    result["committed_transaction_operation_count"] = distribution([n for c in cases for n in c["committed_basic_operation_counts"]])
    revalidations = [c["top1_formal_revalidation"] for c in cases if c["top1_formal_revalidation"]]
    result["top1_formal_revalidation"] = {
        "cases": len(revalidations), "recorded_paths": sum(r["recorded_paths"] for r in revalidations),
        "recorded_bond_cleavage_paths": sum(r["recorded_bond_cleavage_paths"] for r in revalidations),
        **{key: sum(bool(r[key]) for r in revalidations) for key in (
            "regeneration_complete", "exact_path_multiset_match", "supported_peaks_match",
            "score_recomputed_exactly", "verification_flags_all_true")},
    }
    result["sessions"] = {
        "count": len(sessions), "missing_or_ambiguous_receipts": sum(not s["audit_available"] for s in sessions),
        "with_format_errors": sum(s.get("format_errors", 0) > 0 for s in sessions),
        "with_repeated_format_errors": sum(s.get("format_errors", 0) > 1 for s in sessions),
        "format_errors": sum(s.get("format_errors", 0) for s in sessions),
        "counted_format_error_events": sum(s.get("counted_format_error_events", 0) for s in sessions),
        "failure_categories": dict(Counter(s["failure_category"] for s in sessions if s.get("failure_category"))),
        "trial_records": sum(s.get("trial_records", 0) for s in sessions),
        "qualifying_trials": sum(s.get("qualifying_trials", 0) for s in sessions),
        "selected_trials": sum(s.get("selected_trials", 0) for s in sessions),
        "selection_note": "Selected trial IDs are not committed edits; committed transactions come only from authoritative candidate histories",
        "submission_trace_note": "Preflight and submit can both record one logical submission; trace records are not independent submissions or API calls",
    }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    run = args.run.resolve()
    output = args.output.resolve()
    if output == run or run in output.parents:
        raise ValueError("Audit output must be outside the original run directory")
    if not (run / "attempt.json").exists() or not (run / "summary.json").exists():
        raise ValueError("Run must have its completed launcher attempt and summary before audit")
    manifest, ledger, summary = (read(run / name) for name in ("manifest.json", "logs/budget.json", "summary.json"))
    if ledger.get("pending_reservations"):
        raise ValueError("Cannot audit with in-flight requests")
    if source_versions() != manifest["versions"]:
        raise ValueError("Frozen scientific source differs from the current revalidation tools")
    paths = [run / name for name in ("manifest.json", "logs/budget.json", "summary.json", "attempt.json")]
    paths += [run / name for name in ("input.json", "protocol.json", "tested_source.zip") if (run / name).exists()]
    paths += sorted((run / "results").glob("*.json")) + sorted((run / "checkpoints").glob("*.json"))
    fingerprints = {str(path): sha(path) for path in paths}
    results = [read(path) for path in sorted((run / "results").glob("*.json"))]
    expected = manifest["sample_ids"]
    if len(results) != summary["processed_samples"] or len(expected) != summary["requested_samples"]:
        raise ValueError("Summary/result counts disagree")
    ids = [r["sample_id"] for r in results]
    if len(ids) != len(set(ids)) or set(ids) - set(expected):
        raise ValueError("Unexpected or duplicate result IDs")
    cases, sessions = [audit_case(r) for r in results], audit_sessions(run, ledger)
    groups = {}
    for group in ("all_processed", "complete", "incomplete"):
        subset = [c for c in cases if group == "all_processed" or c["workflow_complete"] == (group == "complete")]
        sample_ids = {c["sample_id"] for c in subset}
        groups[group] = summarize(subset, [s for s in sessions if s["sample_id"] in sample_ids])
    violations = [{"sample_id": c["sample_id"], "invalid_emitted_candidate_ids": c["invalid_emitted_candidate_ids"],
                   "graph_failures": c["final_graph_validation_failures"],
                   "quality_metadata_matches": c["quality_metadata_recomputed_exactly"],
                   "committed_count_matches": c["committed_count_matches_saved_counter"],
                   "top1_formal_revalidation_passed": c["top1_formal_revalidation_passed"]}
                  for c in cases if c["invalid_emitted_candidate_ids"] or c["final_graph_validation_failures"]
                  or not c["quality_metadata_recomputed_exactly"] or not c["committed_count_matches_saved_counter"]
                  or c["top1_formal_revalidation_passed"] is False]
    if fingerprints != {str(path): sha(path) for path in paths} or source_versions() != manifest["versions"]:
        raise ValueError("Inputs or scientific code changed during audit")
    report = {
        "created_utc": datetime.now(timezone.utc).isoformat(), "run": str(run), "labels_read": False,
        "api_calls": 0, "requested_cases": len(expected), "processed_cases": len(cases),
        "unprocessed_sample_ids": [sid for sid in expected if sid not in ids],
        "scope": "Development regression quality audit; no identification accuracy claims without separate frozen-label evaluation",
        "interpretation": ["All quality case counts use the stated group denominator; Top1-specific flags are false when Top1 is null.",
                           "Workflow completion, legal SMILES, spectral support and correct molecular identity are separate outcomes.",
                           "Below-50-percent flags describe center/upper intensity coverage; they do not alter acceptance or ranking.",
                           "Replaying the same rule engine checks reproducibility and its formal bookkeeping, not independent chemical correctness.",
                           "No model narrative or hidden thinking was interpreted; mechanism and molecular identity remain unconfirmed."],
        "groups": groups, "consistency_violations": violations, "cases": cases, "sessions": sessions,
        "input_sha256": fingerprints, "audit_script_sha256": sha(__file__),
    }
    write_json(output, report)
    print(json.dumps({"output": str(output), "cases": len(cases), "sessions": len(sessions),
                      "consistency_violations": len(violations), "labels_read": False, "api_calls": 0}, ensure_ascii=False))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
