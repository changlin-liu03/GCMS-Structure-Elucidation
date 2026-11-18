"""Host-controlled, spectrum-sharing review batches with atomic case recovery.

The model still runs inside Claude Agent SDK. Only tool-validated submissions
reach the deterministic acceptance code. Each batch is billed exactly once.
"""
import asyncio
import copy
import json
import time

from gcms_agent.config import digest
from gcms_agent.chemistry.smiles_io import parse_smiles
from gcms_agent.chemistry.molecode_adapter import render_graph
from gcms_agent.chemistry.graph_edit import apply_patch
from gcms_agent.scoring.acceptance import accept_edit
from gcms_agent.tools.server import check_review
from gcms_agent.tools.batch_server import CHECKS, exploration_trial_ids
from gcms_agent.workflow.candidate import assess, graph_identity
from gcms_agent.workflow.search_state import remember_best, finalize_best, resume_exploration, final_status
from gcms_agent.workflow.termination import stop_reason
from gcms_agent.workflow.budget import BudgetExhausted
from gcms_agent.harness.hooks import redact
from gcms_agent.workflow.exploration import record_exploration, record_audit, exploration_summary
from gcms_agent.scoring.ranking import evidence_profile

RESTARTABLE = {"budget_exhausted", "offpeak_wait", "reservation_exceeded", "configuration_error", "service_error",
               "tool_error", "usage_error", "authentication_error", "billing_error"}
FATAL = {"budget_exhausted", "offpeak_wait", "reservation_exceeded", "configuration_error", "service_error", "usage_error",
         "authentication_error", "billing_error"}


def initialize_candidate(cid, smiles, peaks, assessment_cache):
    parsed = parse_smiles(smiles)
    if parsed["graph"] is None:
        return {"candidate_id": cid, "original_smiles": smiles, "final_smiles": None,
                "validation": {"valid": False, "errors": parsed["errors"]}, "score": None,
                "result_status": "invalid_unresolved", "stop_reason": "syntax_unparseable",
                "finished": True, "history": [], "accepted_edits": 0}
    graph = parsed["graph"]
    validation, evidence, score = cached_assess(graph, peaks, None, assessment_cache)
    composition = {}
    for atom in graph["atoms"]:
        if atom["element"] != "H":
            composition[atom["element"]] = composition.get(atom["element"], 0) + 1
    return {"candidate_id": cid, "original_smiles": smiles, "graph": graph,
            "initial_graph": copy.deepcopy(graph), "composition": composition,
            "validation": validation, "evidence": evidence, "score": score,
            "initial_validation": copy.deepcopy(validation), "initial_score": copy.deepcopy(score),
            "rounds_completed": 0, "stagnation_count": 0, "accepted_edits": 0,
            "format_errors": 0, "graph_version": 0, "history": [],
            "seen": [graph_identity(graph, validation)], "elapsed_seconds": 0, "finished": False}


def cached_assess(graph, peaks, composition, cache):
    # Exact graph hash deliberately preserves atom IDs. A canonical SMILES key
    # would require remapping every atom-level explanation before reuse.
    key = digest([graph, peaks, composition])
    if key not in cache:
        cache[key] = assess(graph, peaks, composition)
    return copy.deepcopy(cache[key])


def bounded_trial_memory(memory, graph_version):
    """Never forget an unsubmitted qualifying trial on the current graph."""
    def pending(item):
        return (item.get("base_version") == graph_version
                and (item.get("verdict") or {}).get("improves_best", (item.get("verdict") or {}).get("accepted")) is True
                and item.get("selected") is not True)
    unresolved = [item for item in memory if pending(item)]
    recent_other = [item for item in memory if not pending(item)][-20:]
    return unresolved + recent_other


def candidate_payload(state, runtime):
    return {"candidate_id": state["candidate_id"], "graph": state["graph"],
            "molecode": render_graph(state["graph"]), "validation": state["validation"],
            "evidence": state["evidence"], "score": state["score"],
            "round": state["rounds_completed"] + 1, "graph_version": state["graph_version"],
            "previous_feedback": state["history"][-3:],
            "best_score": (state.get("best_state") or state).get("score"),
            "seen": state.get("seen", []),
            "trial_memory": bounded_trial_memory(state.get("trial_memory", []), state["graph_version"]),
            "format_repairs_remaining": max(0, runtime["format_retries"] - state["format_errors"])}


def split_batches(states, runtime):
    from gcms_agent.harness.compact import compact_candidate
    size = runtime.get("batch_size", 4)
    limit = runtime.get("max_batch_chars", 20000)
    batches, batch, characters = [], [], 0
    for state in states:
        length = len(json.dumps(compact_candidate(candidate_payload(state, runtime)), ensure_ascii=False))
        if batch and (len(batch) >= size or characters + length > limit):
            batches.append(batch)
            batch, characters = [], 0
        batch.append(state["candidate_id"])
        characters += length
    if batch:
        batches.append(batch)
    return batches


def finish(state, reason):
    finalize_best(state)
    valid = state["validation"].get("valid", False)
    state.update(final_smiles=state["validation"].get("canonical_smiles") if valid else None,
                 result_status=final_status(state),
                 stop_reason=reason, finished=True)
    state["spectral_status"] = "supported" if (state.get("score") or {}).get("score", 0) > 0 else "spectrally_unsupported"
    state["evidence_status"] = evidence_profile(state)["evidence_status"]


def remember_trials(states, audit):
    """Only compact facts enter future prompts; full operations stay in receipts."""
    from gcms_agent.harness.compact import compact_score
    for trial in audit.get("trials", []):
        cid = trial.get("candidate_id")
        if cid not in states:
            continue
        result = trial.get("result") or {}
        memory = states[cid].setdefault("trial_memory", [])
        item = {"trial_id": trial["trial_id"], "base_version": trial.get("base_version"),
                "graph_hash": trial.get("graph_hash"), "spectrum_hash": trial.get("spectrum_hash"),
                "rules_version": trial.get("rules_version"),
                "validation_valid": (result.get("validation") or {}).get("valid") is True,
                "evidence_complete": (result.get("evidence") or {}).get("complete") is True,
                "canonical_smiles": (result.get("validation") or {}).get("canonical_smiles"),
                "score": compact_score(result.get("score")), "verdict": trial.get("verdict"),
                "selected": trial.get("selected", False)}
        memory[:] = [previous for previous in memory if previous["trial_id"] != item["trial_id"]] + [item]
        memory[:] = bounded_trial_memory(memory, states[cid]["graph_version"])


def apply_review(state, review, peaks, config, receipt_key, assessment_cache):
    # Revalidate the expanded tool contract at the host boundary, including
    # one nonempty explanation per operation and observed peak references.
    core = {key: review[key] for key in ("decision", "proposals", "summary")}
    check_review(core, {**candidate_payload(state, config["runtime"]), "peaks": peaks})
    outcomes, accepted = [], []
    snapshot = copy.deepcopy(state["graph"])
    stop_evidence = review.get("stop_evidence") or {}
    checks = stop_evidence.get("checks") or []
    checked_names = ({name for name, passed in checks.items() if passed is True} if isinstance(checks, dict)
                     else set(checks) if isinstance(checks, list) and all(isinstance(name, str) for name in checks) else set())
    explored = exploration_trial_ids(state, spectrum_hash=digest(peaks),
        rules_version=digest({key: config[key] for key in ("chemistry", "scoring")}))
    pending_trials = [trial for trial in state.get("trial_memory", [])
                      if trial.get("base_version") == state["graph_version"]
                      and (trial.get("verdict") or {}).get("improves_best", (trial.get("verdict") or {}).get("accepted")) is True
                      and not trial.get("selected", False)]
    exploration_complete = bool(explored) and checked_names == set(CHECKS) and not pending_trials
    for proposal in review["proposals"]:
        entry = {"proposal": proposal, "base_version": state["graph_version"]}
        try:
            proposed = apply_patch(snapshot, proposal["operations"])
            validation, evidence, score = cached_assess(proposed, peaks, state["composition"], assessment_cache)
            identity = graph_identity(proposed, validation)
            if identity in state["seen"]:
                verdict = {"accepted": False, "reason": "duplicate_structure"}
            else:
                state["seen"].append(identity)
                verdict = accept_edit(state["validation"], validation, state["score"], score)
            entry.update(validation=validation, score=score, verdict=verdict, proposed_graph=proposed)
            if verdict["accepted"]:
                accepted.append((proposed, validation, evidence, score, entry))
        except (ValueError, TypeError, KeyError) as exc:
            entry.update(verdict={"accepted": False, "reason": "validation_error"}, error=str(exc))
        outcomes.append(entry)
    remember_best(state)
    if accepted:
        chosen = sorted(accepted, key=lambda a: (a[3] is None, -(a[3] or {}).get("score", 0),
                          -(a[3] or {}).get("score_lower", 0), a[1]["canonical_smiles"]))[0]
        state.update(graph=chosen[0], validation=chosen[1], evidence=chosen[2], score=chosen[3])
        state["graph_version"] += 1
        state["accepted_edits"] += 1
        remember_best(state)
        state["stagnation_count"] = 0
        chosen[4]["committed"] = True
        if chosen[3] is None:
            finish(state, "tool_error")
    elif exploration_complete:
        state["stagnation_count"] += 1
    else:
        # A bare keep, invalid attempt or no-op is not evidence of convergence.
        # The fixed round/time/budget limits still bound an unproductive search.
        state["stagnation_count"] = 0
    state["rounds_completed"] += 1
    if "trial_memory" in state:
        state["trial_memory"] = bounded_trial_memory(state["trial_memory"], state["graph_version"])
    state["history"].append({"round": state["rounds_completed"], "summary": review["summary"],
                             "outcomes": outcomes, "review_receipt": receipt_key,
                             "stop_evidence": review.get("stop_evidence")})
    if not accepted and not exploration_complete:
        state["history"][-1]["exploration_feedback"] = {
            "status": "exploration_insufficient", "exploration_trial_ids": explored,
            "missing_checks": sorted(set(CHECKS) - checked_names),
            "pending_trial_ids": [trial["trial_id"] for trial in pending_trials],
            "message": "A completed review needs all checks and a distinct chemically valid alternative with complete spectral evidence; invalid/no-op/zero trials do not establish stagnation. Resolve any pending qualifying trial."}
    # Completion follows verified evidence, not the model's optional stop hint.
    # Recheck the same gates at commit time, including retained pending trials.
    checked_stop = (stop_evidence.get("verified") is True
                    and exploration_complete
                    and not stop_evidence.get("unsubmitted_qualifying_trial_ids") and not pending_trials)
    if (checked_stop and review["decision"] == "keep" and not review["proposals"]
            and state["validation"].get("valid") and not state.get("finished")):
        finish(state, "review_complete")


CASE_STOPS = {"case_budget_exhausted", "case_session_limit", "case_transport_failure_limit"}


def _halt_batches(reason, scheduling, control):
    if reason in FATAL or reason in CASE_STOPS:
        scheduling.setdefault("halt_reason", reason)
        if reason in FATAL and control is not None:
            control["fatal_reason"] = reason


def _apply_failed_attempt(states, event):
    remember_trials(states, event.get("audit", {}))
    for state in states.values():
        state["format_errors"] += event.get("format_errors", 0)
        state["history"].append({"round": state["rounds_completed"] + 1,
                                 "error_category": event["error_category"],
                                 "error_type": event.get("error_type", "HarnessError"),
                                 "failure_receipt": event["receipt_key"]})


async def _store_attempt_receipt(checkpoints, key, receipt, budget, request_id, usage):
    try:
        checkpoints.save(key, receipt)
    except Exception:
        # Even a failed response journal must not strand a paid in-flight
        # reservation. The durable request intent prevents automatic resends.
        await budget.settle(request_id, usage, failure=receipt if "error_category" in receipt else None)
        raise
    await budget.settle(request_id, usage, failure=receipt if "error_category" in receipt else None)


async def _execute_batch(pending, envelope, case_key, row, peaks, config, client,
                         budget, checkpoints, trial_cache, scheduling, control):
    """Run one independent intent; only the serial coordinator edits candidates.

    Every paid attempt has a durable request ID. Both success and failure
    receipts precede settlement, so replay can reconcile billing without
    reissuing a completed request. An interrupted request with no receipt is
    reported as unknown usage rather than silently sent a second time.
    """
    from gcms_agent.workflow.policy import session_policy, review_client

    group = {cid: copy.deepcopy(envelope["states"][cid]) for cid in pending["ids"]}
    if any(digest(s["graph"]) != pending["hashes"][cid] for cid, s in group.items()):
        raise ValueError("Pending review graph version no longer matches the case checkpoint")
    failures = []
    for key in pending.setdefault("failure_receipts", []):
        event = checkpoints.load(key)
        if event is None or event["hashes"] != pending["hashes"]:
            raise ValueError("Missing or mismatched failed-attempt receipt")
        await budget.settle(event["request_id"], event.get("usage", {"known": False}), failure=event)
        failures.append(event)
        _apply_failed_attempt(group, event)

    session_config, tier = session_policy(config, list(group.values()))
    runtime = session_config["runtime"]
    batch_client = review_client(client, session_config)
    payload = {"sample_id": row["sample_id"], "peaks": peaks,
               "case_exploration": exploration_summary(envelope.get("exploration_archive", {})),
               "candidates": [candidate_payload(s, runtime) for s in group.values()],
               "rules_version": digest({key: config[key] for key in ("chemistry", "scoring")}),
               "max_trial_batches": runtime.get("max_trial_batches", 4),
               "trial_cache": trial_cache, "review_tier": tier}
    receipt_key = pending["receipt_key"]
    elapsed = pending.get("elapsed_seconds", 0.0)

    def result(review=None, failure=None):
        return {"review": review, "failure": failure, "elapsed_seconds": elapsed,
                "failures": failures}

    def terminal(reason):
        pending["failure"] = reason
        pending["elapsed_seconds"] = elapsed
        _halt_batches(reason, scheduling, control)
        checkpoints.save(case_key, envelope)
        return result(failure=reason)

    receipt = checkpoints.load(receipt_key)
    if receipt is not None:
        if receipt["hashes"] != pending["hashes"]:
            raise ValueError("Stored batch receipt refers to different graphs")
        review = receipt["review"]
        await budget.settle(receipt["request_id"], review.get("usage", {"known": False}))
        elapsed = receipt.get("elapsed_seconds", 0)
        return result(review=review)

    # Recover the gap between persisting an attempt receipt and updating the
    # case intent. This also handles an interrupted, unknowable provider call.
    request_id = pending.get("in_flight")
    if request_id:
        failure_key = request_id + "_failure"
        event = checkpoints.load(failure_key)
        if event is None:
            previous = next((r for r in budget.ledger["requests"] if r["request_id"] == request_id), None)
            event = {"receipt_key": failure_key, "request_id": request_id,
                     "hashes": pending["hashes"], "error_category": "service_error",
                     "error_type": "InterruptedRequest", "usage": previous["usage"] if previous else {"known": False},
                     "detail": "Interrupted request has no durable response; it was not automatically resent.",
                     "retryable": False, "format_errors": 0, "audit": {}, "elapsed_seconds": elapsed}
            checkpoints.save(failure_key, event)
        if event["hashes"] != pending["hashes"]:
            raise ValueError("Stored failure receipt refers to different graphs")
        await budget.settle(request_id, event.get("usage", {"known": False}), failure=event)
        if failure_key not in pending["failure_receipts"]:
            pending["failure_receipts"].append(failure_key)
            failures.append(event)
            _apply_failed_attempt(group, event)
        elapsed = event.get("elapsed_seconds", elapsed)
        pending.update(in_flight=None, elapsed_seconds=elapsed)
        checkpoints.save(case_key, envelope)
        if not event.get("retryable") or pending["attempt"] > runtime["network_retries"]:
            return terminal(event["error_category"])
    if pending.get("failure"):
        return terminal(pending["failure"])
    if failures and (not failures[-1].get("retryable") or pending["attempt"] > runtime["network_retries"]):
        return terminal(failures[-1]["error_category"])

    while pending["attempt"] <= runtime["network_retries"]:
        reason = scheduling.get("halt_reason") or (control or {}).get("fatal_reason")
        if reason or scheduling.get("aborting"):
            return terminal(reason or "tool_error")
        if any(s["format_errors"] > runtime["format_retries"] for s in group.values()):
            return terminal("format_retry_exhausted")
        remaining = min(runtime["candidate_timeout_seconds"] - s["elapsed_seconds"]
                        for s in group.values()) - elapsed
        if remaining <= 0:
            return terminal("timeout")
        # Receipt-specific IDs avoid races on the shared ledger's list length.
        request_id = f"{receipt_key}_a{pending['attempt'] + 1}"
        try:
            await budget.reserve_review(request_id, payload, runtime)
        except BudgetExhausted as exc:
            return terminal(getattr(exc, "category", "budget_exhausted"))
        # A reservation may wait for another batch's settlement. Recheck the
        # stop flag after that wait before starting any new provider request.
        reason = scheduling.get("halt_reason") or (control or {}).get("fatal_reason")
        if reason or scheduling.get("aborting"):
            await budget.release(request_id)
            return terminal(reason or "tool_error")
        pending.update(attempt=pending["attempt"] + 1, in_flight=request_id)
        try:
            checkpoints.save(case_key, envelope)
        except BaseException:
            scheduling["aborting"] = True
            pending.update(attempt=pending["attempt"] - 1, in_flight=None)
            await budget.release(request_id)
            raise
        started = time.monotonic()
        try:
            payload["candidates"] = [candidate_payload(s, runtime) for s in group.values()]
            review = await asyncio.wait_for(batch_client.review_batch(payload), timeout=max(.1, remaining))
        except Exception as exc:
            elapsed += time.monotonic() - started
            failure = "timeout" if isinstance(exc, asyncio.TimeoutError) else getattr(exc, "category", "tool_error")
            failure_key = request_id + "_failure"
            event = {"receipt_key": failure_key, "request_id": request_id, "hashes": pending["hashes"],
                     "error_category": failure, "error_type": type(exc).__name__,
                     "usage": getattr(exc, "usage", {"known": False}),
                     "detail": redact(getattr(exc, "detail", None) or "")[:800],
                     "retryable": bool(getattr(exc, "retryable", False)) and failure != "pre_tool_no_progress",
                     "format_errors": getattr(exc, "format_errors", 0),
                     "audit": getattr(exc, "audit", {}) or {}, "elapsed_seconds": elapsed}
            await _store_attempt_receipt(checkpoints, failure_key, event, budget, request_id, event["usage"])
            pending["failure_receipts"].append(failure_key)
            pending.update(in_flight=None, elapsed_seconds=elapsed)
            failures.append(event)
            _apply_failed_attempt(group, event)
            checkpoints.save(case_key, envelope)
            if event["retryable"] and pending["attempt"] <= runtime["network_retries"]:
                await asyncio.sleep(min(2 ** (pending["attempt"] - 1), 4))
                continue
            return terminal(failure)
        elapsed += time.monotonic() - started
        # Persistence is outside the transport handler: a disk failure must
        # never become another paid call, even while sibling batches finish.
        receipt = {"hashes": pending["hashes"], "request_id": request_id,
                   "review": review, "elapsed_seconds": elapsed, "review_tier": tier}
        await _store_attempt_receipt(checkpoints, receipt_key, receipt, budget, request_id,
                                     review.get("usage", {"known": False}))
        # With monetary limits disabled, overruns are accounting diagnostics,
        # not a reason to suppress subsequent review sessions.
        if (budget.limit is not None or budget.case_limit is not None) and budget.ledger.get("reservation_exceeded"):
            _halt_batches("reservation_exceeded", scheduling, control)
        return result(review=review)
    return terminal("service_error")


def _commit_batch(pending, outcome, states, peaks, config, assessment_cache):
    """Commit an entire receipt to a private case copy before its atomic save."""
    group = {cid: states[cid] for cid in pending["ids"]}
    for event in outcome["failures"]:
        _apply_failed_attempt(group, event)
    elapsed = outcome["elapsed_seconds"]
    for state in group.values():
        state["elapsed_seconds"] += elapsed
    if outcome["failure"]:
        for state in group.values():
            finish(state, "format_retry_exhausted" if state["format_errors"] > config["runtime"]["format_retries"]
                   else outcome["failure"])
        return
    review = outcome["review"]
    remember_trials(group, review.get("audit", {}))
    entries = review.get("reviews", [])
    ids = [entry.get("candidate_id") for entry in entries if isinstance(entry, dict)]
    complete = len(ids) == len(entries) == len(group) and set(ids) == set(group)
    try:
        if not complete:
            raise ValueError("Batch review must cover each candidate exactly once")
        for entry in entries:
            check_review({k: entry[k] for k in ("decision", "proposals", "summary")},
                         {**candidate_payload(group[entry["candidate_id"]], config["runtime"]), "peaks": peaks})
    except (ValueError, KeyError, TypeError):
        for state in group.values():
            finish(state, "missing_submission")
        return
    for entry in entries:
        state = group[entry["candidate_id"]]
        state["format_errors"] += review.get("format_errors", 0)
        if state["format_errors"] > config["runtime"]["format_retries"]:
            finish(state, "format_retry_exhausted")
        else:
            apply_review(state, entry, peaks, config, pending["receipt_key"], assessment_cache)
            state["history"][-1]["models"] = review.get("actual_models", [])


async def process_case(row, jobs, peaks, config, client, budget, checkpoints, control=None):
    """Review disjoint batches concurrently; commit each wave atomically.

    The default width is one. Version-1 single-batch intents are replayed as a
    one-batch wave. A barrier between waves preserves initial-review coverage
    before later rounds, and all in-flight attempts drain before errors escape.
    """
    runtime = config["runtime"]
    concurrency = runtime.get("batch_concurrency", 1)
    if type(concurrency) is not int or concurrency < 1:
        raise ValueError("runtime.batch_concurrency must be a positive integer")
    case_key = f"{row['sample_id']}__case"
    identity = digest([row["sample_id"], jobs, peaks])
    envelope = checkpoints.load(case_key)
    assessment_cache, trial_cache, scheduling = {}, {}, {}
    if envelope is None:
        envelope = {"identity": identity, "states": {cid: initialize_candidate(cid, smiles, peaks, assessment_cache)
                    for cid, smiles in jobs}, "sequence": 0, "pending": None}
    elif envelope["identity"] != identity:
        raise ValueError("Case checkpoint input differs from the current case")
    if envelope.get("checkpoint_version", 1) not in (1, 2):
        raise ValueError("Unsupported case checkpoint version")
    if envelope.get("pending") and "batches" not in envelope["pending"]:
        envelope["pending"] = {"batches": [envelope["pending"]]}
    envelope["checkpoint_version"] = 2
    states = envelope["states"]
    archive = envelope.setdefault("exploration_archive", {})
    for cid, state in states.items():
        record_exploration(archive, cid, state.get("validation") or {}, state.get("score"))
    for state in states.values():
        if state.get("finished") and state.get("stop_reason") in RESTARTABLE | CASE_STOPS:
            resume_exploration(state)
            state["finished"] = False
            state.pop("stop_reason", None)
    checkpoints.save(case_key, envelope)

    while True:
        if envelope.get("pending") is None:
            for state in states.values():
                if state.get("finished"):
                    continue
                reason = scheduling.get("halt_reason") or (control or {}).get("fatal_reason")
                if not reason and state["validation"].get("valid") and not state["evidence"].get("complete", False):
                    reason = "tool_error"
                reason = reason or stop_reason(state, runtime)
                if not reason and state["elapsed_seconds"] >= runtime["candidate_timeout_seconds"]:
                    reason = "timeout"
                if reason:
                    finish(state, reason)
            active = [s for s in states.values() if not s.get("finished")]
            if not active:
                break
            round_min = min(s["rounds_completed"] for s in active)
            groups = split_batches([s for s in active if s["rounds_completed"] == round_min], runtime)[:concurrency]
            batches = []
            for group in groups:
                envelope["sequence"] += 1
                batches.append({"ids": group, "receipt_key": f"{case_key}_b{envelope['sequence']:04d}_review",
                                "hashes": {cid: digest(states[cid]["graph"]) for cid in group}, "attempt": 0})
            envelope["pending"] = {"batches": batches}
            checkpoints.save(case_key, envelope)
        batches = envelope["pending"]["batches"]

        async def execute(pending):
            try:
                return await _execute_batch(pending, envelope, case_key, row, peaks, config, client,
                                            budget, checkpoints, trial_cache, scheduling, control)
            except BaseException:
                scheduling["aborting"] = True
                raise

        tasks = [asyncio.create_task(execute(pending)) for pending in batches]
        completed = asyncio.gather(*tasks, return_exceptions=True)
        try:
            outcomes = await asyncio.shield(completed)
        except asyncio.CancelledError:
            # Cancellation stops new work but allows already paid calls to save
            # their receipts and settle. Resume then commits those receipts.
            scheduling["aborting"] = True
            await completed
            raise
        errors = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
        if errors:
            raise errors[0]
        # Do not leave mutated in-memory states visible to a later worker if
        # validation or persistence fails partway through the commit.
        committed = copy.deepcopy(envelope)
        for pending, outcome in zip(batches, outcomes):
            _commit_batch(pending, outcome, committed["states"], peaks, config, assessment_cache)
            for event in outcome["failures"]:
                record_audit(committed["exploration_archive"], event.get("audit", {}))
            if outcome["review"]:
                record_audit(committed["exploration_archive"], outcome["review"].get("audit", {}))
        committed["pending"] = None
        checkpoints.save(case_key, committed)
        envelope, states = committed, committed["states"]
    checkpoints.save(case_key, envelope)
    for cid, state in states.items():
        checkpoints.save(f"{row['sample_id']}_{cid}", state)
    return [states[cid] for cid, _ in jobs]


