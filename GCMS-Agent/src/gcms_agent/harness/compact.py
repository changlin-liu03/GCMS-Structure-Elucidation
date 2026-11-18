"""Lossless scientific facts in a small model-facing view; full audits stay local."""
from copy import deepcopy
from collections import Counter
import json

from gcms_agent.chemistry.molecode_adapter import render_graph
from gcms_agent.data.intensity import spectrum_weights


def reject_forbidden_fields(value):
    # Lazy import also permits prompts.py to call compact_batch.
    from gcms_agent.harness.prompts import reject_forbidden_fields as reject
    reject(value)


def compact_validation(validation):
    return {k: deepcopy(validation[k]) for k in (
        "valid", "errors", "canonical_smiles", "formula", "nominal_mass",
        "exact_mass", "composition", "hydrogen_count", "formal_charge",
    ) if k in validation}


def compact_score(score, *, include_unassigned=True):
    if score is None:
        return None
    fields = ("score", "score_lower", "score_upper", "supported_peaks", "intensity_basis")
    if include_unassigned:
        fields += ("unassigned_peaks",)
    return {k: deepcopy(score[k]) for k in fields if k in score}


def evidence_index(evidence):
    """Stable local indices let the model request complete paths when needed."""
    if not evidence:
        return {"complete": False, "paths": []}
    columns = ["evidence_id", "peak_mz", "rule_id", "ion_formula", "broken_bonds", "steps"]
    rows = []
    for i, item in enumerate(evidence.get("evidence", [])):
        row = [f"e{i + 1}", *(deepcopy(item.get(k)) for k in columns[1:])]
        rows.append(row)
    diagnostics = evidence.get("diagnostics") or {}
    result = {"complete": evidence.get("complete", False), "path_columns": columns, "paths": rows,
            "diagnostics": {k: deepcopy(diagnostics[k]) for k in (
                "error", "rule_version", "molecular_nominal_mass", "molecular_mass_peak_present",
                "isotopes", "limitations",
            ) if isinstance(diagnostics, dict) and k in diagnostics}}
    return result


def compact_history(history):
    result = []
    for entry in history[-3:]:
        row = {k: deepcopy(entry[k]) for k in ("round", "error_category", "error_type", "exploration_feedback") if k in entry}
        outcomes = []
        for outcome in entry.get("outcomes", []):
            item = {k: deepcopy(outcome[k]) for k in ("verdict", "committed", "error", "trial_id") if k in outcome}
            validation = outcome.get("validation") or {}
            item["structure"] = validation.get("canonical_smiles")
            # Unsupported peaks are the full spectrum minus supported_peaks.
            # Keep the explicit list on the current candidate only, not in
            # every historic outcome and trial of the same spectrum.
            item["score"] = compact_score(outcome.get("score"), include_unassigned=False)
            outcomes.append(item)
        row["outcomes"] = outcomes
        if entry.get("error_category"):
            row["error_category"] = entry["error_category"]
        result.append(row)
    return result


def retained_trial_memory(memory, graph_version):
    """Never evict an unresolved qualifying trial for the current graph."""
    unique = {}
    for item in memory:
        trial_id = item.get("trial_id")
        if trial_id is not None:
            # Latest selection status wins while retaining deterministic order.
            unique.pop(trial_id, None)
            unique[trial_id] = item
    current_pending, others = [], []
    for item in unique.values():
        pending = (item.get("base_version") == graph_version
                   and (item.get("verdict") or {}).get("accepted") is True
                   and not item.get("selected", False))
        (current_pending if pending else others).append(item)
    return current_pending + others[-20:]


def _reference_repeated_scores(candidate):
    """Factor exact duplicate score facts without rounding or changing IDs."""
    containers = [outcome for entry in candidate.get("previous_feedback", [])
                  for outcome in entry.get("outcomes", [])]
    containers += candidate.get("trial_memory", [])

    def signature(score):
        return json.dumps(score, sort_keys=True, separators=(",", ":"), allow_nan=False)

    current = compact_score(candidate.get("score"), include_unassigned=False)
    current_key = signature(current) if current is not None else None
    counts = Counter(signature(row["score"]) for row in containers if row.get("score") is not None)
    assigned, references = {}, {}
    for row in containers:
        score = row.get("score")
        if score is None:
            continue
        key = signature(score)
        if key == current_key:
            row["score_ref"] = "current"
        elif counts[key] > 1:
            if key not in assigned:
                assigned[key] = f"s{len(assigned) + 1}"
                references[assigned[key]] = score
            row["score_ref"] = assigned[key]
        else:
            continue
        del row["score"]
    if references:
        candidate["score_references"] = references


def compact_candidate(payload):
    from gcms_agent.scoring.ranking import evidence_profile
    reject_forbidden_fields(payload)
    graph = payload.get("graph")
    molecode = payload.get("molecode") or (render_graph(graph) if graph else None)
    result = {"candidate_id": payload["candidate_id"], "graph_version": payload["graph_version"],
              "molecode": molecode, "validation": compact_validation(payload.get("validation") or {}),
              "score": compact_score(payload.get("score")), "evidence": evidence_index(payload.get("evidence")),
              "round": payload.get("round", 1),
              "previous_feedback": compact_history(payload.get("previous_feedback", []))}
    profile = evidence_profile(payload)
    result["evidence_assessment"] = {k: profile[k] for k in (
        "evidence_status", "unassigned_intensity_fraction", "strongest_unassigned_peaks")}
    if "best_score" in payload:
        result["historical_best_score"] = compact_score(payload["best_score"])
    if payload.get("seen"):
        result["seen_structures"] = list(payload["seen"])
    if payload.get("trial_memory"):
        result["trial_memory"] = [{k: deepcopy(item[k]) for k in (
            "trial_id", "base_version", "canonical_smiles", "verdict", "selected",
            "validation_valid", "evidence_complete",
        ) if k in item} | {"score": compact_score(item.get("score"), include_unassigned=False)}
            for item in retained_trial_memory(payload["trial_memory"], payload["graph_version"])]
    _reference_repeated_scores(result)
    return result


def compact_batch(payload):
    # Private cache is intentionally never traversed or copied into model input.
    public = {k: v for k, v in payload.items() if k != "trial_cache"}
    reject_forbidden_fields(public)
    result = {"sample_id": payload["sample_id"],
            "spectrum_columns": ["mz", "intensity"],
            "spectrum": [[p["mz"], p["intensity"]] for p in payload["peaks"]],
            "intensity_encoding": {"type": "raw_linear", "normalization": "none", "quantization_bounds": False},
            "candidates": [compact_candidate(item) for item in payload["candidates"]],
            "exploration_policy": "valid_unseen_structures_allowed_without_score_gain; historical_best_preserved",
            "trial_batches_remaining": payload.get("max_trial_batches", 4)}
    # This avoids a spectrum lookup solely to learn dominant peak weights.
    # Preserve the original two-column spectrum contract and exact floats;
    # absent intensities in synthetic inputs are not silently fabricated.
    if payload["peaks"] and all("intensity" in peak for peak in payload["peaks"]):
        result["spectrum_weights"] = spectrum_weights(payload["peaks"])
    if "case_exploration" in payload:
        result["case_exploration"] = deepcopy(payload["case_exploration"])
    if "session_budget" in payload:
        budget = payload["session_budget"]
        if not isinstance(budget, dict):
            raise ValueError("session_budget must be an object")
        # Only host scheduling limits, never arbitrary execution state, enter
        # the model view. These are not estimates of SDK turns remaining.
        result["session_budget"] = {}
        for name in ("max_tool_turns", "reserve_submit_turns", "max_non_submission_tool_calls"):
            if name in budget:
                value = budget[name]
                if type(value) is not int or value < 0:
                    raise ValueError("session_budget limits must be nonnegative integers")
                result["session_budget"][name] = value
    return result


def compact_trial(result, baseline_score=None):
    score = result.get("score")
    return {"validation": compact_validation(result.get("validation") or {}),
            "score": compact_score(score, include_unassigned=False), "evidence": evidence_index(result.get("evidence")),
            "new_supported_peaks": sorted(set((score or {}).get("supported_peaks", [])) -
                                          set((baseline_score or {}).get("supported_peaks", [])))}
