"""Case-scoped, compact MCP reviews with immutable trial references."""
from copy import deepcopy
import json
import time

from gcms_agent.config import digest
from gcms_agent.data.intensity import spectrum_weights
from gcms_agent.harness.compact import compact_candidate, compact_trial
from gcms_agent.harness.prompts import reject_forbidden_fields
from gcms_agent.harness.hooks import redact
from gcms_agent.scoring.acceptance import accept_edit
from .server import OPERATION_SCHEMA, _keys, check_operations, check_base_version, check_review
from .spectral import analyze_proposal


CHECKS = ("graph_read", "valence_checked", "spectrum_checked", "alternatives_checked")


def exploration_trial_ids(candidate, *, spectrum_hash, rules_version, records=()):
    """Verify tool-audited, distinct, chemically valid alternatives in this scope.

    Invalid attempts and no-ops do not establish that alternatives were
    explored. Historical summaries qualify only when the host retained the
    validation, evidence-completeness and immutable trial scope together.
    """
    parent_smiles = (candidate.get("validation") or {}).get("canonical_smiles")
    graph_hash = digest(candidate["graph"])
    eligible = set()
    for record in [*records, *candidate.get("trial_memory", [])]:
        if (record.get("base_version") != candidate["graph_version"]
                or record.get("graph_hash") != graph_hash
                or record.get("spectrum_hash") != spectrum_hash
                or record.get("rules_version") != rules_version):
            continue
        if "result" in record:
            result = record.get("result") or {}
            validation = result.get("validation") or {}
            valid = validation.get("valid") is True
            complete = (result.get("evidence") or {}).get("complete") is True
            smiles = validation.get("canonical_smiles")
        else:
            valid = record.get("validation_valid") is True
            complete = record.get("evidence_complete") is True
            smiles = record.get("canonical_smiles")
        if valid and complete and smiles and smiles != parent_smiles and record.get("trial_id"):
            eligible.add(record["trial_id"])
    return sorted(eligible)


TRIAL_SCHEMA = {"type": "object", "additionalProperties": False,
    "properties": {"candidate_id": {"type": "string"}, "base_version": {"type": "integer", "minimum": 0},
                   "operations": {"type": "array", "minItems": 1, "items": OPERATION_SCHEMA}},
    "required": ["candidate_id", "base_version", "operations"]}
REFERENCE_SCHEMA = {"type": "object", "additionalProperties": False,
    "properties": {"trial_id": {"type": "string"}, "reason": {"type": "string", "minLength": 1},
                   "peak_refs": {"type": "array", "items": {"type": "integer"}},
                   "operation_explanations": {"type": "array", "items": {"type": "string", "minLength": 1}}},
    "required": ["trial_id", "reason", "peak_refs", "operation_explanations"]}
ITEM_SCHEMA = {"type": "object", "additionalProperties": False,
    "properties": {"candidate_id": {"type": "string"}, "decision": {"enum": ["keep", "edit"]},
                   "proposals": {"type": "array", "maxItems": 3, "items": REFERENCE_SCHEMA},
                   "summary": {"type": "string", "minLength": 1},
                   "stop_review": {"type": "boolean", "description": "Deprecated compatibility hint: omit it. The host alone decides completion from verified evidence."},
                   "checks": {"type": "array", "uniqueItems": True, "items": {"enum": list(CHECKS)}}},
    "required": ["candidate_id", "decision", "proposals", "summary"]}
BATCH_REVIEW_SCHEMA = {"type": "object", "additionalProperties": False,
    "properties": {"reviews": {"type": "array", "minItems": 1, "items": ITEM_SCHEMA}}, "required": ["reviews"]}


class BatchReviewTools:
    def __init__(self, payload, library, max_format_repairs=2):
        public = {k: v for k, v in payload.items() if k != "trial_cache"}
        reject_forbidden_fields(public)
        self.payload = deepcopy(public)
        self.cache = payload.get("trial_cache")
        if self.cache is None:
            self.cache = {}
        self.library = library
        self.candidates = {c["candidate_id"]: c for c in self.payload["candidates"]}
        if not self.candidates or len(self.candidates) != len(self.payload["candidates"]):
            raise ValueError("Batch must contain distinct candidate IDs")
        self.submitted = None
        self.format_errors = 0
        self.max_format_repairs = max_format_repairs
        self.preflight_errors = {}
        self.submission_attempts = []
        self.submission_attempt_count = 0
        self.trials = {}
        self.trial_batches = 0
        self.max_trial_batches = payload.get("max_trial_batches", 4)
        if type(self.max_trial_batches) is not int or self.max_trial_batches < 1:
            raise ValueError("max_trial_batches must be positive")
        self.tool_metrics = []
        self.session_budget = payload.get("session_budget")
        self.non_submission_tool_calls = 0
        self.observed_model_responses = 0
        self.response_ids = set()
        self.unidentified_model_messages = 0
        if self.session_budget is not None:
            cap = self.session_budget.get("max_non_submission_tool_calls")
            if type(cap) is not int or cap < 1:
                raise ValueError("max_non_submission_tool_calls must be positive")

    def observe_model_response(self, message_id):
        # SDK messages may split thinking/text/tool blocks from the same API
        # response. Count stable response IDs once. With no ID the message
        # count is a conservative signal, never an asserted SDK turn count.
        if isinstance(message_id, str) and message_id:
            if message_id in self.response_ids:
                return
            self.response_ids.add(message_id)
        else:
            self.unidentified_model_messages += 1
        self.observed_model_responses += 1

    def session_control(self):
        cap = (self.session_budget or {}).get("max_non_submission_tool_calls")
        remaining = max(0, cap - self.non_submission_tool_calls) if cap is not None else None
        submit_only = bool(self.submitted is not None or (cap is not None and
                          (remaining == 0 or self.observed_model_responses >= cap)))
        prefer_submit = (submit_only or self.trial_batches >= self.max_trial_batches or
                         (cap is not None and (remaining <= 1 or self.observed_model_responses >= cap - 1)))
        return {"max_non_submission_tool_calls": cap,
                "non_submission_tool_calls": self.non_submission_tool_calls,
                "non_submission_tool_calls_remaining": remaining,
                "observed_model_responses": self.observed_model_responses,
                "response_count_policy": "unique_message_id_else_conservative_message_count; not SDK num_turns",
                "unidentified_model_messages": self.unidentified_model_messages,
                "trial_batches_remaining": max(0, self.max_trial_batches - self.trial_batches),
                "submit_only": submit_only,
                "preferred_action": ("end_session" if self.submitted is not None else
                                     "submit_reviews" if prefer_submit else "analyze_or_submit"),
                "message": ("Reviews recorded; end this session." if self.submitted is not None else
                            "Submit all candidates now using recorded trial IDs. Do not claim review completion without verified checks."
                            if prefer_submit else "Keep tool calls focused and submit as soon as this round is reviewed.")}

    def exploration_denial(self, name):
        if name in {"submit_reviews", "mcp__gcms__submit_reviews"}:
            return None
        if self.submitted is not None:
            return "Reviews already submitted; end this session."
        if self.session_control()["submit_only"]:
            return "Submission reserve reached. Call submit_reviews now; no further exploration is allowed in this session."
        return None

    def _candidate(self, cid):
        if cid not in self.candidates:
            raise ValueError("Unknown candidate_id for this batch")
        return self.candidates[cid]

    def get_candidate(self, args):
        _keys(args, {"candidate_id", "detail"}, {"candidate_id"}, "candidate lookup")
        candidate = self._candidate(args["candidate_id"])
        detail = args.get("detail", "compact")
        if detail == "compact":
            return compact_candidate(candidate)
        if detail == "graph":
            return {"candidate_id": args["candidate_id"], "graph_version": candidate["graph_version"],
                    "graph": deepcopy(candidate["graph"])}
        raise ValueError("detail must be compact or graph")

    def get_spectrum(self, args):
        _keys(args, {"mz"}, set(), "spectrum lookup")
        peaks = self.payload["peaks"]
        requested = args.get("mz")
        if requested is not None:
            if not isinstance(requested, list) or any(type(mz) is not int for mz in requested):
                raise ValueError("mz must be an array of observed integer masses")
            observed = {p["mz"] for p in peaks}
            if any(mz not in observed for mz in requested):
                raise ValueError("mz must reference observed peaks")
        rows = [{**deepcopy(p), "weight": weight} for p, weight in zip(peaks, spectrum_weights(peaks))
                if requested is None or p["mz"] in requested]
        return {"peaks": rows, "weight_normalization": "raw intensity divided by the sum over the entire spectrum",
                "intensity_encoding": "raw_linear", "intensity_normalization": "none",
                "measurement_uncertainty_modeled": False,
                "total_peak_count": len(peaks)}

    def analyze(self, args):
        if self.submitted is not None:
            raise ValueError("Reviews already submitted; end this session")
        _keys(args, {"proposals"}, {"proposals"}, "trial batch")
        proposals = args["proposals"]
        if not isinstance(proposals, list) or not proposals:
            raise ValueError("proposals must be a nonempty list")
        if self.trial_batches >= self.max_trial_batches:
            return {"results": [], "trial_budget_exhausted": True,
                    "message": "Trial batch limit reached. Submit reviewed candidates using recorded trial IDs."}
        # Validate the envelope before any expensive work; one malformed item cannot hide a partial trial batch.
        counts = {}
        for proposal in proposals:
            _keys(proposal, TRIAL_SCHEMA["properties"], TRIAL_SCHEMA["required"], "trial")
            candidate = self._candidate(proposal["candidate_id"])
            check_base_version(proposal, candidate)
            check_operations(proposal["operations"], candidate["graph"])
            counts[proposal["candidate_id"]] = counts.get(proposal["candidate_id"], 0) + 1
        if max(counts.values()) > 3:
            raise ValueError("At most three alternatives per candidate in one trial batch")
        self.trial_batches += 1
        results = []
        for proposal in proposals:
            candidate = self._candidate(proposal["candidate_id"])
            scope = {"sample_id": self.payload["sample_id"], "candidate_id": proposal["candidate_id"],
                     "base_version": candidate["graph_version"], "graph_hash": digest(candidate["graph"]),
                     "rules_version": self.payload.get("rules_version", "unspecified"),
                     "spectrum_hash": digest(self.payload["peaks"]), "operations": proposal["operations"]}
            trial_id = "t_" + digest(scope)[:24]
            cache_key = digest({k: v for k, v in scope.items() if k not in ("candidate_id", "base_version")})
            repeated = trial_id in self.trials
            cache_hit = cache_key in self.cache
            if not repeated:
                try:
                    result = deepcopy(self.cache[cache_key]) if cache_hit else analyze_proposal(
                        candidate["graph"], proposal["operations"], self.payload["peaks"])
                    if result.get("evidence") and not result["evidence"].get("complete", False):
                        result["score"] = None
                    if not cache_hit:
                        self.cache[cache_key] = deepcopy(result)
                    verdict = accept_edit(candidate.get("validation") or {}, result["validation"],
                                          candidate.get("score"), result.get("score"))
                    if result.get("validation", {}).get("valid") and not (result.get("evidence") or {}).get("complete", False):
                        verdict = {"accepted": False, "reason": "evidence_incomplete"}
                    if result.get("validation", {}).get("canonical_smiles") in candidate.get("seen", []):
                        verdict = {"accepted": False, "reason": "duplicate_structure"}
                    best_score = candidate.get("best_score", candidate.get("score"))
                    verdict["improves_best"] = bool(verdict["accepted"] and result.get("score") is not None
                        and (best_score is None or result["score"]["score"] > best_score["score"]))
                    self.trials[trial_id] = {**deepcopy(scope), "trial_id": trial_id, "result": result,
                                            "verdict": verdict, "cache_hit": cache_hit, "selected": False}
                except (ValueError, TypeError, KeyError, RuntimeError) as exc:
                    self.trials[trial_id] = {**deepcopy(scope), "trial_id": trial_id,
                        "result": {"validation": {"valid": False, "errors": [redact(str(exc))]}, "score": None, "evidence": None},
                        "verdict": {"accepted": False, "reason": "validation_error"}, "cache_hit": False, "selected": False}
            record = self.trials[trial_id]
            results.append({"candidate_id": proposal["candidate_id"], "trial_id": trial_id,
                "operation_count": len(record["operations"]),
                **compact_trial(record["result"], candidate.get("score")), "verdict": deepcopy(record["verdict"]),
                "cached": cache_hit or repeated, "no_new_information": repeated})
        return {"results": results, "trial_batches_remaining": self.max_trial_batches - self.trial_batches}

    def get_evidence(self, args):
        _keys(args, {"candidate_id", "trial_id", "evidence_ids"}, {"candidate_id", "evidence_ids"}, "evidence lookup")
        candidate = self._candidate(args["candidate_id"])
        evidence = candidate.get("evidence") or {}
        if args.get("trial_id"):
            record = self._trial(args["trial_id"], args["candidate_id"])
            evidence = record["result"].get("evidence") or {}
        ids = args["evidence_ids"]
        if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids):
            raise ValueError("evidence_ids must be an array of local evidence IDs")
        paths = {f"e{i+1}": item for i, item in enumerate(evidence.get("evidence", []))}
        if any(item not in paths for item in ids):
            raise ValueError("Unknown evidence_id for this candidate/trial")
        return {"candidate_id": args["candidate_id"], "trial_id": args.get("trial_id"),
                "evidence": {item: deepcopy(paths[item]) for item in ids}}

    def _trial(self, trial_id, cid):
        candidate = self._candidate(cid)
        record = self.trials.get(trial_id)
        if not record or record["candidate_id"] != cid or record["sample_id"] != self.payload["sample_id"]:
            raise ValueError("Unknown or cross-candidate trial_id")
        if record["base_version"] != candidate["graph_version"] or record["graph_hash"] != digest(candidate["graph"]):
            raise ValueError("stale_graph_version: trial no longer matches candidate")
        if record["rules_version"] != self.payload.get("rules_version", "unspecified"):
            raise ValueError("Trial rule version mismatch")
        return record

    def validate_submission(self, review):
        reject_forbidden_fields(review)
        _keys(review, {"reviews"}, {"reviews"}, "batch review")
        items = review["reviews"]
        if not isinstance(items, list) or len(items) != len(self.candidates):
            raise ValueError("Review must cover every candidate exactly once")
        seen, expanded = set(), []
        for item in items:
            _keys(item, ITEM_SCHEMA["properties"], ITEM_SCHEMA["required"], "candidate review")
            cid = item["candidate_id"]
            candidate = self._candidate(cid)
            if cid in seen:
                raise ValueError("Duplicate candidate_id in submission")
            seen.add(cid)
            references = item["proposals"]
            if not isinstance(references, list) or len(references) > 3:
                raise ValueError("At most three final proposals per candidate")
            operations = []
            selected = []
            for ref in references:
                _keys(ref, REFERENCE_SCHEMA["properties"], REFERENCE_SCHEMA["required"], "trial reference")
                record = self._trial(ref["trial_id"], cid)
                if ref["trial_id"] in selected:
                    raise ValueError("Duplicate trial reference")
                selected.append(ref["trial_id"])
                operations.append({"base_version": record["base_version"], "operations": deepcopy(record["operations"]),
                    **{k: deepcopy(ref[k]) for k in ("reason", "peak_refs", "operation_explanations")}})
            checked = check_review({"decision": item["decision"], "proposals": operations, "summary": item["summary"]},
                                   {**candidate, "peaks": self.payload["peaks"]})
            stop = item.get("stop_review", False)
            checks = item.get("checks", [])
            if type(stop) is not bool or not isinstance(checks, list) or any(c not in CHECKS for c in checks) or len(set(checks)) != len(checks):
                raise ValueError("Invalid stop_review or checks")
            records = [r for r in self.trials.values() if r["candidate_id"] == cid]
            qualifying = {r["trial_id"] for r in records if r["verdict"].get("improves_best", r["verdict"]["accepted"])}
            # A new SDK session must not erase an improvement discovered in an
            # earlier review of this unchanged graph. Historical IDs need a new
            # analyze call to enter this registry, but still prohibit early stop.
            qualifying.update(t["trial_id"] for t in candidate.get("trial_memory", [])
                              if t.get("base_version") == candidate["graph_version"]
                              and (t.get("verdict") or {}).get("improves_best", (t.get("verdict") or {}).get("accepted")) is True
                              and not t.get("selected", False))
            unresolved = sorted(qualifying - set(selected))
            explored = exploration_trial_ids(candidate, records=records,
                spectrum_hash=digest(self.payload["peaks"]),
                rules_version=self.payload.get("rules_version", "unspecified"))
            current_ids = {r["trial_id"] for r in records}
            historic_duplicate = bool(set(explored) - current_ids)
            # The model reports its review and checks; the host determines
            # completion. Omitting/declining the optional stop hint must not
            # force another paid review when all evidence gates already pass.
            verified_stop = (checked["decision"] == "keep" and candidate.get("validation", {}).get("valid", False)
                             and set(checks) == set(CHECKS) and bool(explored) and not unresolved)
            # The deprecated model hint never vetoes an otherwise valid round
            # submission. It also never grants completion: the host gates above
            # remain authoritative and are rechecked at commit time.
            ignored_hint = stop and not verified_stop
            expanded.append({"candidate_id": cid, **checked, "stop_review": bool(verified_stop),
                "stop_evidence": {"source": "host_evidence_policy", "model_stop_requested": stop,
                                  "hint_ignored": ignored_hint,
                                  "host_stop_feedback": ("Stop hint ignored: this round was submitted, but completion requires a valid kept parent, all checks, a distinct valid assessed alternative and no pending qualifying trial."
                                                         if ignored_hint else None),
                                  "checks": checks, "trial_ids": [r["trial_id"] for r in records],
                                  "exploration_trial_ids": explored,
                                  "selected_trial_ids": selected, "historic_duplicate": historic_duplicate,
                                  "unsubmitted_qualifying_trial_ids": unresolved, "verified": bool(verified_stop)}})
        return {"reviews": expanded}

    def record_submission_attempt(self, review, *, phase, accepted, reason=None,
                                  tool_use_id=None, counted_format_error=False):
        """Persist structural diagnostics, never free-form model explanations."""
        try:
            input_hash = digest(review)
        except (ValueError, TypeError):
            input_hash = None
        items = review.get("reviews") if isinstance(review, dict) else None
        shapes = []
        if isinstance(items, list):
            for item in items[:10]:
                if not isinstance(item, dict):
                    shapes.append({"item_type": type(item).__name__})
                    continue
                cid = item.get("candidate_id")
                proposals = item.get("proposals")
                refs = []
                if isinstance(proposals, list):
                    for ref in proposals[:4]:
                        if not isinstance(ref, dict):
                            refs.append({"reference_type": type(ref).__name__})
                            continue
                        tid = ref.get("trial_id")
                        trial = self.trials.get(tid) if isinstance(tid, str) else None
                        explanations = ref.get("operation_explanations")
                        refs.append({"fields": sorted(redact(str(k))[:80] for k in ref)[:16],
                                     "trial_id": tid if trial else None,
                                     "known_trial": trial is not None,
                                     "expected_operation_count": len(trial["operations"]) if trial else None,
                                     "operation_explanations_count": len(explanations) if isinstance(explanations, list) else None})
                shapes.append({"candidate_id": cid if isinstance(cid, str) and cid in self.candidates else None,
                               "decision": item.get("decision") if item.get("decision") in ("keep", "edit") else None,
                               "proposal_count": len(proposals) if isinstance(proposals, list) else None,
                               "stop_hint": item.get("stop_review") if type(item.get("stop_review")) is bool else None,
                               "references": refs})
        self.submission_attempt_count += 1
        self.submission_attempts.append({"attempt": self.submission_attempt_count, "phase": phase,
            "tool_use_id": redact(tool_use_id)[:160] if tool_use_id is not None else None,
            "accepted": accepted, "reason": redact(reason)[:800] if reason else None,
            "counted_format_error": counted_format_error, "format_errors_after": self.format_errors,
            "input_hash": input_hash, "review_count": len(items) if isinstance(items, list) else None,
            "reviews": shapes})
        self.submission_attempts[:] = self.submission_attempts[-64:]

    def preflight(self, review, tool_use_id):
        if tool_use_id is not None and tool_use_id in self.preflight_errors:
            self.record_submission_attempt(review, phase="preflight_replay", accepted=False,
                reason=self.preflight_errors[tool_use_id], tool_use_id=tool_use_id)
            raise ValueError(self.preflight_errors[tool_use_id])
        if self.format_errors > self.max_format_repairs:
            self.record_submission_attempt(review, phase="preflight", accepted=False,
                reason="format_retry_exhausted", tool_use_id=tool_use_id)
            raise ValueError("format_retry_exhausted")
        try:
            self.validate_submission(review)
        except (ValueError, TypeError, KeyError) as exc:
            self.format_errors += 1
            if tool_use_id is not None:
                self.preflight_errors[tool_use_id] = redact(str(exc))[:800]
            self.record_submission_attempt(review, phase="preflight", accepted=False,
                reason=str(exc), tool_use_id=tool_use_id, counted_format_error=True)
            raise
        self.record_submission_attempt(review, phase="preflight", accepted=True, tool_use_id=tool_use_id)

    def submit(self, review):
        if self.format_errors > self.max_format_repairs:
            self.record_submission_attempt(review, phase="submit", accepted=False, reason="format_retry_exhausted")
            raise ValueError("format_retry_exhausted")
        try:
            result = self.validate_submission(review)
            if self.submitted is not None and result != self.submitted:
                raise ValueError("Reviews already submitted")
        except (ValueError, TypeError, KeyError) as exc:
            self.format_errors += 1
            self.record_submission_attempt(review, phase="submit", accepted=False,
                reason=str(exc), counted_format_error=True)
            raise
        self.submitted = result
        self.record_submission_attempt(review, phase="submit", accepted=True)
        for item in result["reviews"]:
            for trial_id in item["stop_evidence"]["selected_trial_ids"]:
                self.trials[trial_id]["selected"] = True
        return {"submitted": True, "candidate_ids": sorted(self.candidates), "message": "Reviews recorded; end this session.",
                "host_stop_feedback": [{"candidate_id": item["candidate_id"],
                    "verified": item["stop_evidence"]["verified"],
                    "hint_ignored": item["stop_evidence"]["hint_ignored"],
                    "message": item["stop_evidence"]["host_stop_feedback"]} for item in result["reviews"]]}

    def audit(self):
        return {"trials": deepcopy(list(self.trials.values())), "tool_metrics": deepcopy(self.tool_metrics),
                "trial_batches": self.trial_batches, "format_errors": self.format_errors,
                "preflight_errors": {redact(k)[:160]: redact(v)[:800] for k, v in self.preflight_errors.items()},
                "submission_attempts": deepcopy(self.submission_attempts),
                "submission_attempt_count": self.submission_attempt_count,
                "cache_hits": sum(r.get("cache_hit", False) for r in self.trials.values()),
                "session_control": self.session_control()}


def build_batch_server(sdk, payload, library, max_format_repairs=2):
    state = BatchReviewTools(payload, library, max_format_repairs)
    definitions = [
        ("get_spectrum", "Inspect observed m/z and original input-scale linear intensities, plus separate full-spectrum scoring weights; optionally select observed m/z values without changing intensity scale or renormalizing weights.",
         {"type": "object", "additionalProperties": False, "properties": {
             "mz": {"type": "array", "items": {"type": "integer"}, "uniqueItems": True}}}, state.get_spectrum),
        ("get_candidate", "Read a candidate's compact facts or full atom graph only when needed.",
         {"type": "object", "additionalProperties": False, "properties": {"candidate_id": {"type": "string"},
          "detail": {"enum": ["compact", "graph"]}}, "required": ["candidate_id"]}, state.get_candidate),
        ("read_reference", "Read one approved reference only when its detail is needed.",
         {"type": "object", "additionalProperties": False, "properties": {"reference_id": {"type": "string", "enum": list(library.references)}},
          "required": ["reference_id"]}, lambda args: library.read_reference(args["reference_id"])),
        ("analyze_proposals", "Batch-test independent transactions on current candidate snapshots. Returns immutable trial IDs, validation, score and evidence; does not commit.",
         {"type": "object", "additionalProperties": False, "properties": {"proposals": {"type": "array", "minItems": 1, "items": TRIAL_SCHEMA}},
          "required": ["proposals"]}, state.analyze),
        ("get_evidence", "Retrieve full fragmentation paths by local evidence IDs for a candidate or its trial.",
         {"type": "object", "additionalProperties": False, "properties": {"candidate_id": {"type": "string"},
          "trial_id": {"type": "string"}, "evidence_ids": {"type": "array", "items": {"type": "string"}}},
          "required": ["candidate_id", "evidence_ids"]}, state.get_evidence),
        ("submit_reviews", "Submit every candidate exactly once; edit proposals reference trial IDs and explain each operation. Required to finish.",
         BATCH_REVIEW_SCHEMA, state.submit),
    ]
    def wrap(name, fn):
        async def handler(args):
            started = time.monotonic()
            error = False
            try:
                denial = state.exploration_denial(name)
                if denial:
                    raise ValueError(denial)
                if name != "submit_reviews":
                    # This synchronous guard also enforces the cap if several
                    # PreToolUse hooks are admitted before handlers execute.
                    state.non_submission_tool_calls += 1
                result = fn(args)
            except (ValueError, TypeError, KeyError) as exc:
                result, error = {"error": redact(str(exc))}, True
            result = ({**result, "session_control": state.session_control()} if isinstance(result, dict)
                      else {"result": result, "session_control": state.session_control()})
            encoded = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            state.tool_metrics.append({"tool": name, "input_chars": len(json.dumps(args, ensure_ascii=False)),
                                       "output_chars": len(encoded), "error": error,
                                       "elapsed_seconds": time.monotonic() - started})
            value = {"content": [{"type": "text", "text": encoded}]}
            if error:
                value["is_error"] = True
            return value
        return handler
    tool_list = [sdk.tool(name, description, schema)(wrap(name, fn)) for name, description, schema, fn in definitions]
    server = sdk.create_sdk_mcp_server(name="gcms", version="0.2.0", tools=tool_list)
    return server, state, {f"mcp__gcms__{name}" for name, *_ in definitions}
