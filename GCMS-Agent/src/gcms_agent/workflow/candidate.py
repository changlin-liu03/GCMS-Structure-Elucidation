import asyncio
import copy
import time
from gcms_agent.config import digest
from gcms_agent.chemistry.smiles_io import parse_smiles
from gcms_agent.chemistry.validation import validate_graph
from gcms_agent.chemistry.graph_edit import apply_patch
from gcms_agent.chemistry.molecode_adapter import render_graph
from gcms_agent.fragmentation.enumeration import analyze_spectrum
from gcms_agent.scoring.support import calculate_support
from gcms_agent.scoring.acceptance import accept_edit
from gcms_agent.workflow.termination import stop_reason
from gcms_agent.workflow.search_state import remember_best, finalize_best, resume_exploration, final_status
from gcms_agent.workflow.budget import BudgetExhausted

def assess(graph, peaks, composition=None):
    validation = validate_graph(graph, expected_composition=composition)
    evidence = analyze_spectrum(graph, peaks) if validation["valid"] else {"evidence": [], "supported_peaks": [], "diagnostics": ["Chemically invalid graph is not scored"], "complete": True}
    score = calculate_support(peaks, evidence["supported_peaks"]) if validation["valid"] and evidence.get("complete") else None
    return validation, evidence, score

def graph_identity(graph, validation):
    return validation.get("canonical_smiles") or digest(graph)

async def process_candidate(row, cid, smiles, peaks, config, client, budget, checkpoints, mode="agent", control=None):
    key = f"{row['sample_id']}_{cid}"
    state = checkpoints.load(key)
    if state and state.get("finished"):
        if state.get("stop_reason") not in {"budget_exhausted", "offpeak_wait", "reservation_exceeded", "configuration_error", "service_error", "tool_error", "usage_error", "authentication_error", "billing_error"}:
            return state
        resume_exploration(state)
        state["finished"] = False
    if state is None:
        initial = parse_smiles(smiles)
        if initial["graph"] is None:
            state = {"candidate_id": cid, "original_smiles": smiles, "final_smiles": None, "validation": {"valid": False, "errors": initial["errors"]}, "score": None, "result_status": "invalid_unresolved", "stop_reason": "syntax_unparseable", "finished": True, "history": []}
            checkpoints.save(key, state)
            return state
        validation, evidence, score = assess(initial["graph"], peaks)
        composition = {}
        for atom in initial["graph"]["atoms"]:
            if atom["element"] != "H":
                composition[atom["element"]] = composition.get(atom["element"], 0) + 1
        state = {"candidate_id": cid, "original_smiles": smiles, "graph": initial["graph"], "initial_graph": initial["graph"], "composition": composition,
                 "validation": validation, "evidence": evidence, "score": score, "rounds_completed": 0, "stagnation_count": 0,
                 "accepted_edits": 0, "format_errors": 0, "graph_version": 0, "history": [], "seen": [graph_identity(initial["graph"], validation)],
                 "elapsed_seconds": 0, "initial_validation": copy.deepcopy(validation), "initial_score": copy.deepcopy(score)}
    runtime = config["runtime"]
    started = time.monotonic()
    prior_elapsed = state.get("elapsed_seconds", 0)
    reason = "baseline_only" if mode != "agent" else None
    while reason is None:
        if control is not None and control.get("fatal_reason"):
            reason = control["fatal_reason"]
            break
        if state["validation"].get("valid") and not state["evidence"].get("complete", False):
            reason = "tool_error"
            break
        reason = stop_reason(state, runtime)
        if reason:
            break
        remaining = runtime["candidate_timeout_seconds"] - prior_elapsed - (time.monotonic() - started)
        if remaining <= 0:
            reason = "timeout"
            break
        payload = {"sample_id": row["sample_id"], "candidate_id": cid, "graph": state["graph"], "molecode": render_graph(state["graph"]),
                   "validation": state["validation"], "evidence": state["evidence"], "score": state["score"], "round": state["rounds_completed"]+1,
                   "previous_feedback": state["history"][-3:], "peaks": peaks, "graph_version": state["graph_version"],
                   "format_repairs_remaining": max(0, runtime["format_retries"]-state["format_errors"]),
                   "seen": state.get("seen", []), "best_score": (state.get("best_state") or state).get("score")}
        receipt_key = f"{key}_r{state['rounds_completed']+1}_review"
        receipt = checkpoints.load(receipt_key)
        if receipt and receipt["graph_hash"] != digest(state["graph"]):
            raise ValueError("Stored review receipt refers to a different graph")
        review = receipt["review"] if receipt else None
        if receipt:
            await budget.settle(receipt["request_id"], review.get("usage", {"known":False}))
        for retry in ([] if receipt else range(runtime["network_retries"]+1)):
            if control is not None and control.get("fatal_reason"):
                reason = control["fatal_reason"]
                break
            request_id = f"{key}_r{state['rounds_completed']+1}_try{len(budget.ledger['requests'])}_{retry}"
            try:
                await budget.reserve_review(request_id, payload, runtime)
            except BudgetExhausted as exc:
                reason = exc.category
                if control is not None:
                    control["fatal_reason"] = reason
                break
            try:
                review = await asyncio.wait_for(client.review(payload), timeout=max(.1, remaining))
                checkpoints.save(receipt_key, {"graph_hash":digest(state["graph"]), "request_id":request_id, "review":review})
                await budget.settle(request_id, review.get("usage", {"known": False}))
                break
            except Exception as exc:
                failure = {"error_category": getattr(exc, "category", "tool_error"),
                           "detail": getattr(exc, "detail", ""), "audit": getattr(exc, "audit", {})}
                await budget.settle(request_id, getattr(exc, "usage", {"known": False}), failure=failure)
                state["format_errors"] += getattr(exc, "format_errors", 0)
                category = "timeout" if isinstance(exc, asyncio.TimeoutError) else getattr(exc, "category", "tool_error")
                state["history"].append({"round": state["rounds_completed"]+1, "error_category": category, "error_type": type(exc).__name__, "detail": getattr(exc, "detail", "")})
                if getattr(exc, "retryable", False) and retry < runtime["network_retries"]:
                    await asyncio.sleep(min(2**retry, 4))
                    remaining = runtime["candidate_timeout_seconds"]-prior_elapsed-(time.monotonic()-started)
                    continue
                reason = "format_retry_exhausted" if state["format_errors"] > runtime["format_retries"] else category
                if control is not None and reason in {"authentication_error", "billing_error", "configuration_error", "usage_error", "service_error"}:
                    control["fatal_reason"] = reason
                break
        if reason:
            break
        state["format_errors"] += review.get("format_errors", 0)
        if state["format_errors"] > runtime["format_retries"]:
            reason = "format_retry_exhausted"
            break
        proposals = review.get("proposals", [])
        if len(proposals) > runtime["proposals_per_round"]:
            reason = "format_retry_exhausted"
            break
        accepted, outcomes = [], []
        snapshot = copy.deepcopy(state["graph"])
        for proposal in proposals:
            entry = {"proposal": proposal, "base_version": state["graph_version"]}
            try:
                if type(proposal.get("base_version")) is not int or proposal["base_version"] != state["graph_version"]:
                    raise ValueError("Proposal references a stale graph version")
                proposed = apply_patch(snapshot, proposal["operations"])
                validation, evidence, score = assess(proposed, peaks, state["composition"])
                identity = graph_identity(proposed, validation)
                if identity in state["seen"]:
                    verdict = {"accepted": False, "reason": "duplicate_structure"}
                else:
                    state["seen"].append(identity)
                    verdict = accept_edit(state["validation"], validation, state["score"], score)
                entry.update(validation=validation, score=score, verdict=verdict, proposed_graph=proposed)
                if verdict["accepted"]:
                    accepted.append((proposed, validation, evidence, score, entry))
            except Exception as exc:
                entry.update(verdict={"accepted": False, "reason": "validation_error"}, error=str(exc))
            outcomes.append(entry)
        remember_best(state)
        if accepted:
            chosen = sorted(accepted, key=lambda a: (a[3] is None, -(a[3] or {}).get("score", 0), -(a[3] or {}).get("score_lower", 0), a[1]["canonical_smiles"]))[0]
            state.update(graph=chosen[0], validation=chosen[1], evidence=chosen[2], score=chosen[3])
            state["graph_version"] += 1
            state["accepted_edits"] += 1
            remember_best(state)
            state["stagnation_count"] = 0
            chosen[4]["committed"] = True
            if chosen[3] is None:
                reason = "tool_error"
        else:
            state["stagnation_count"] += 1
        state["rounds_completed"] += 1
        state["history"].append({"round": state["rounds_completed"], "summary": review.get("summary", ""), "outcomes": outcomes, "models": review.get("actual_models", []), "usage": review.get("usage", {})})
        state["elapsed_seconds"] = prior_elapsed + time.monotonic()-started
        checkpoints.save(key, state)
    finalize_best(state)
    valid = state["validation"]["valid"]
    state.update(final_smiles=state["validation"].get("canonical_smiles") if valid else None,
                 result_status=final_status(state),
                 stop_reason=reason, finished=True, elapsed_seconds=prior_elapsed+time.monotonic()-started)
    state["spectral_status"] = "supported" if state.get("score") and state["score"]["score"] > 0 else "spectrally_unsupported"
    checkpoints.save(key, state)
    return state
