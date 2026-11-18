"""Chemical and protocol invariants of the compact multi-candidate tool surface."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from gcms_agent.chemistry.smiles_io import parse_smiles
from gcms_agent.data.intensity import raw_peaks
from gcms_agent.harness.compact import compact_batch, compact_candidate
from gcms_agent.harness.skill_loader import SkillLibrary
from gcms_agent.tools.batch_server import BatchReviewTools, build_batch_server, CHECKS
from gcms_agent.workflow.candidate import assess


ROOT = Path(__file__).resolve().parents[2]


def batch(ids=("c01",), cache=None):
    # Linear intensities: acetaldehyde's m/z 44 (60) exceeds ethanol's
    # m/z 31 + 46 (35), preserving a genuine improving trial for stop tests.
    peaks = raw_peaks([31, 44, 46], [20, 60, 15])
    graph = parse_smiles("CCO")["graph"]
    validation, evidence, score = assess(graph, peaks)
    candidates = [{"candidate_id": cid, "graph": deepcopy(graph), "validation": deepcopy(validation),
                   "evidence": deepcopy(evidence), "score": deepcopy(score), "graph_version": 0,
                   "round": 1, "previous_feedback": []} for cid in ids]
    return {"sample_id": "synthetic", "peaks": peaks, "candidates": candidates,
            "rules_version": "fixed-rules", "max_trial_batches": 4, "trial_cache": cache if cache is not None else {}}


def edit(cid="c01", noop=False, n=1):
    operations = ([{"op": "set_atom", "id": "Mol_C_1", "hydrogens": 3}] * n if noop else [
        {"op": "set_bond", "a": "Mol_C_2", "b": "Mol_O_1", "order": 2},
        {"op": "set_atom", "id": "Mol_C_2", "hydrogens": 1},
        {"op": "set_atom", "id": "Mol_O_1", "hydrogens": 0},
    ])
    return {"candidate_id": cid, "base_version": 0, "operations": operations}


def keep(cid="c01", stop=False):
    return {"candidate_id": cid, "decision": "keep", "proposals": [], "summary": "No validated improvement",
            "stop_review": stop, "checks": list(CHECKS)}


def rejected_alternative(cid="c01"):
    # Ethanol -> dimethyl ether: chemically valid, completely assessed, but
    # weaker under this synthetic spectrum. Unlike a no-op it tests a rival.
    return {"candidate_id": cid, "base_version": 0, "operations": [
        {"op": "remove_bond", "a": "Mol_C_1", "b": "Mol_C_2"},
        {"op": "set_bond", "a": "Mol_C_1", "b": "Mol_O_1", "order": 1},
        {"op": "set_atom", "id": "Mol_C_2", "hydrogens": 3},
        {"op": "set_atom", "id": "Mol_O_1", "hydrogens": 0},
    ]}


def submit_edit(trial_id, cid="c01", n=3):
    return {"candidate_id": cid, "decision": "edit", "summary": "A supported alternative",
            "proposals": [{"trial_id": trial_id, "reason": "Supports observed molecular ion",
                           "peak_refs": [44], "operation_explanations": ["Bond or accompanying valence correction"] * n}]}


class BatchToolsTests(unittest.TestCase):
    def state(self, payload=None):
        return BatchReviewTools(payload or batch(), SkillLibrary(ROOT))

    def test_submission_reserve_blocks_exploration_but_preserves_valid_trial_submission(self):
        from gcms_agent.harness.hooks import restricted_pre_tool_hook
        payload = batch()
        payload["session_budget"] = {"max_tool_turns": 4, "reserve_submit_turns": 2,
                                     "max_non_submission_tool_calls": 2}
        sdk = SimpleNamespace(
            tool=lambda name, description, schema: lambda handler: SimpleNamespace(name=name, handler=handler),
            create_sdk_mcp_server=lambda **kwargs: kwargs)
        server, state, names = build_batch_server(sdk, payload, SkillLibrary(ROOT))
        handlers = {tool.name: tool.handler for tool in server["tools"]}

        async def run():
            await handlers["get_spectrum"]({})
            result = await handlers["analyze_proposals"]({"proposals": [edit()]})
            data = json.loads(result["content"][0]["text"])
            self.assertTrue(data["results"][0]["verdict"]["accepted"])
            self.assertTrue(data["session_control"]["submit_only"])
            self.assertEqual(data["session_control"]["non_submission_tool_calls_remaining"], 0)
            denied = await handlers["get_candidate"]({"candidate_id": "c01"})
            self.assertTrue(denied["is_error"])
            self.assertEqual(state.non_submission_tool_calls, 2)
            hook = restricted_pre_tool_hook(names, state)
            denial = await hook({"tool_name": "mcp__gcms__analyze_proposals", "tool_input": {}}, "extra", {})
            self.assertEqual(denial["hookSpecificOutput"]["permissionDecision"], "deny")
            review = {"reviews": [submit_edit(data["results"][0]["trial_id"])]}
            self.assertEqual(await hook({"tool_name": "mcp__gcms__submit_reviews", "tool_input": review}, "submit", {}), {})
            submitted = await handlers["submit_reviews"](review)
            self.assertFalse(submitted.get("is_error", False))
            self.assertTrue(state.audit()["trials"][0]["selected"])
            self.assertFalse(state.submitted["reviews"][0]["stop_review"])
            self.assertEqual(state.session_control()["preferred_action"], "end_session")
        asyncio.run(run())

    def test_response_budget_deduplicates_split_message_blocks_and_never_claims_sdk_turns(self):
        payload = batch()
        payload["session_budget"] = {"max_non_submission_tool_calls": 3}
        state = self.state(payload)
        for message_id in ("api1", "api1", "api1", "api2"):
            state.observe_model_response(message_id)
        self.assertEqual(state.observed_model_responses, 2)
        self.assertFalse(state.session_control()["submit_only"])
        state.observe_model_response(None)
        control = state.session_control()
        self.assertTrue(control["submit_only"])
        self.assertEqual(control["unidentified_model_messages"], 1)
        self.assertIn("not SDK num_turns", control["response_count_policy"])
        self.assertIsNotNone(state.exploration_denial("analyze_proposals"))
        self.assertIsNone(state.exploration_denial("submit_reviews"))

    def test_compact_view_has_one_molecode_and_all_peaks_without_private_graphs(self):
        p = batch()
        p["trial_cache"]["sentinel"] = {"true_smiles": "NEVER IN PROMPT"}
        value = compact_batch(p)
        self.assertEqual(value["spectrum"], [[31, 20], [44, 60], [46, 15]])
        self.assertIn("molecode", value["candidates"][0])
        text = json.dumps(value)
        self.assertNotIn('"graph"', text)
        self.assertNotIn("NEVER IN PROMPT", text)
        self.assertNotIn("peak_contributions", text)
        p["candidates"][0]["true_smiles"] = "CC"
        with self.assertRaisesRegex(ValueError, "Forbidden"):
            compact_batch(p)

    def test_spectrum_subset_preserves_raw_intensity_and_full_spectrum_weight(self):
        state = self.state()
        original = deepcopy(state.payload["peaks"])
        full = state.get_spectrum({})
        subset = state.get_spectrum({"mz": [44]})
        self.assertAlmostEqual(sum(p["weight"] for p in full["peaks"]), 1)
        self.assertEqual(subset["peaks"], [p for p in full["peaks"] if p["mz"] == 44])
        self.assertLess(subset["peaks"][0]["weight"], 1)
        self.assertEqual(subset["peaks"][0]["raw_intensity"], 60)
        self.assertEqual(subset["peaks"][0]["lower"], subset["peaks"][0]["intensity"])
        self.assertIn("upper", subset["peaks"][0])
        self.assertEqual(state.payload["peaks"], original)
        for bad in ({"mz": [True]}, {"mz": [999]}, {"mz": "44"}):
            with self.assertRaises(ValueError):
                state.get_spectrum(bad)

    def test_compact_score_preserves_actual_supported_and_unassigned_keys(self):
        item = batch()["candidates"][0]
        view = compact_candidate(item)
        self.assertEqual(view["score"]["supported_peaks"], item["score"]["supported_peaks"])
        self.assertEqual(view["score"]["unassigned_peaks"], item["score"]["unassigned_peaks"])

    def test_trial_submission_expands_operations_and_requires_per_operation_explanations(self):
        state = self.state()
        trial = state.analyze({"proposals": [edit()]})["results"][0]
        self.assertTrue(trial["validation"]["valid"])
        self.assertEqual(trial["validation"]["canonical_smiles"], "CC=O")
        good = submit_edit(trial["trial_id"])
        bad = deepcopy(good)
        bad["proposals"][0]["operation_explanations"].pop()
        with self.assertRaisesRegex(ValueError, "one explanation"):
            state.validate_submission({"reviews": [bad]})
        state.submit({"reviews": [good]})
        self.assertEqual(state.submitted["reviews"][0]["proposals"][0]["operations"], edit()["operations"])
        self.assertTrue(state.audit()["trials"][0]["selected"])

    def test_every_candidate_exactly_once_and_unknown_ids_rejected(self):
        state = self.state(batch(("c01", "c02")))
        for items in ([keep()], [keep(), keep()], [keep(), keep("c99")]):
            with self.assertRaises(ValueError):
                state.validate_submission({"reviews": items})
        state.submit({"reviews": [keep("c02"), keep("c01")]})
        self.assertEqual(len(state.submitted["reviews"]), 2)

    def test_candidate_and_version_isolation(self):
        state = self.state(batch(("c01", "c02")))
        tid = state.analyze({"proposals": [edit()]})["results"][0]["trial_id"]
        with self.assertRaisesRegex(ValueError, "cross-candidate"):
            state.validate_submission({"reviews": [keep(), submit_edit(tid, "c02")]})
        bad = edit()
        bad["base_version"] = 1
        with self.assertRaisesRegex(ValueError, "stale_graph_version"):
            state.analyze({"proposals": [bad]})
        state.candidates["c01"]["graph_version"] = 1
        with self.assertRaisesRegex(ValueError, "stale_graph_version"):
            state.validate_submission({"reviews": [submit_edit(tid), keep("c02")]})

    def test_repeated_trials_use_bound_ids_and_reuse_deterministic_cache(self):
        state = self.state(batch(("c01", "c02")))
        from gcms_agent.tools.spectral import analyze_proposal
        with patch("gcms_agent.tools.batch_server.analyze_proposal", wraps=analyze_proposal) as fn:
            first = state.analyze({"proposals": [edit()]})["results"][0]
            repeated = state.analyze({"proposals": [edit()]})["results"][0]
            other = state.analyze({"proposals": [edit("c02")]})["results"][0]
        self.assertEqual(fn.call_count, 1)
        self.assertEqual(first["trial_id"], repeated["trial_id"])
        self.assertNotEqual(first["trial_id"], other["trial_id"])
        self.assertTrue(repeated["no_new_information"])
        self.assertTrue(other["cached"])

    def test_cache_key_includes_spectrum_and_rules(self):
        cache = {}
        p = batch(cache=cache)
        self.state(p).analyze({"proposals": [edit()]})
        p["rules_version"] = "changed-rules"
        self.state(p).analyze({"proposals": [edit()]})
        p["peaks"] = raw_peaks([31, 44, 46], [29, 10, 20])
        self.state(p).analyze({"proposals": [edit()]})
        self.assertEqual(len(cache), 3)

    def test_stop_requires_recorded_checks_and_no_unsubmitted_improvement(self):
        state = self.state()
        incomplete = state.validate_submission({"reviews": [keep(stop=True)]})["reviews"][0]
        self.assertFalse(incomplete["stop_evidence"]["verified"])
        self.assertTrue(incomplete["stop_evidence"]["hint_ignored"])
        state.analyze({"proposals": [rejected_alternative()]})
        checked = state.validate_submission({"reviews": [keep(stop=True)]})
        self.assertTrue(checked["reviews"][0]["stop_review"])
        state.analyze({"proposals": [edit()]})
        self.assertTrue(any(t["verdict"]["accepted"] for t in state.audit()["trials"]))
        incomplete = state.validate_submission({"reviews": [keep(stop=True)]})["reviews"][0]
        self.assertFalse(incomplete["stop_evidence"]["verified"])
        self.assertTrue(incomplete["stop_evidence"]["unsubmitted_qualifying_trial_ids"])

    def test_completed_review_stops_when_model_hint_is_false_or_omitted(self):
        for include_hint in (True, False):
            with self.subTest(include_hint=include_hint):
                state = self.state()
                state.analyze({"proposals": [rejected_alternative()]})
                item = keep(stop=False)
                if not include_hint:
                    item.pop("stop_review")
                result = state.validate_submission({"reviews": [item]})["reviews"][0]
                self.assertTrue(result["stop_review"])
                self.assertTrue(result["stop_evidence"]["verified"])
                self.assertEqual(result["stop_evidence"]["source"], "host_evidence_policy")
                self.assertFalse(result["stop_evidence"]["model_stop_requested"])

    def test_keep_without_all_checks_or_recorded_trial_does_not_auto_stop(self):
        state = self.state()
        self.assertFalse(state.validate_submission({"reviews": [keep()]})["reviews"][0]["stop_review"])
        state.analyze({"proposals": [rejected_alternative()]})
        for checks in ([], list(CHECKS)[:-1]):
            with self.subTest(checks=checks):
                item = keep()
                item["checks"] = checks
                self.assertFalse(state.validate_submission({"reviews": [item]})["reviews"][0]["stop_review"])

    def test_invalid_trial_and_noop_do_not_prove_exploration(self):
        invalid = edit()
        invalid["operations"] = [{"op": "set_atom", "id": "Mol_C_1", "hydrogens": 5}]
        for proposal in (invalid, edit(noop=True)):
            with self.subTest(proposal=proposal):
                state = self.state()
                state.analyze({"proposals": [proposal]})
                item = state.validate_submission({"reviews": [keep()]})["reviews"][0]
                self.assertFalse(item["stop_evidence"]["verified"])
                self.assertEqual(item["stop_evidence"]["exploration_trial_ids"], [])
                incomplete = state.validate_submission({"reviews": [keep(stop=True)]})["reviews"][0]
                self.assertFalse(incomplete["stop_evidence"]["verified"])
                self.assertTrue(incomplete["stop_evidence"]["hint_ignored"])

    def test_historical_alternative_requires_verified_scope_and_complete_evidence(self):
        from gcms_agent.workflow.case import remember_trials
        payload = batch()
        previous = self.state(payload)
        previous.analyze({"proposals": [rejected_alternative()]})
        candidate = payload["candidates"][0]
        remember_trials({"c01": candidate}, previous.audit())
        result = self.state(payload).validate_submission({"reviews": [keep()]})["reviews"][0]
        self.assertTrue(result["stop_evidence"]["verified"])
        self.assertTrue(result["stop_evidence"]["historic_duplicate"])
        view = compact_candidate(candidate)["trial_memory"][0]
        self.assertTrue(view["validation_valid"])
        self.assertTrue(view["evidence_complete"])
        for key, value in (("validation_valid", False), ("evidence_complete", False),
                           ("graph_hash", "other"), ("spectrum_hash", "other"),
                           ("rules_version", "other"), ("base_version", 99)):
            with self.subTest(key=key):
                changed = deepcopy(payload)
                changed["candidates"][0]["trial_memory"][0][key] = value
                item = self.state(changed).validate_submission({"reviews": [keep()]})["reviews"][0]
                self.assertFalse(item["stop_evidence"]["verified"])

    def test_duplicate_statement_without_audited_trial_does_not_stop(self):
        payload = batch()
        payload["candidates"][0]["previous_feedback"] = [
            {"outcomes": [{"verdict": {"reason": "duplicate_structure"}}]}]
        item = self.state(payload).validate_submission({"reviews": [keep()]})["reviews"][0]
        self.assertFalse(item["stop_evidence"]["verified"])

    def test_formal_edit_cannot_auto_stop_even_with_all_checks(self):
        state = self.state()
        trial = state.analyze({"proposals": [edit()]})["results"][0]
        item = submit_edit(trial["trial_id"])
        item["checks"] = list(CHECKS)
        item["stop_review"] = True
        result = state.validate_submission({"reviews": [item]})["reviews"][0]
        self.assertFalse(result["stop_review"])
        self.assertFalse(result["stop_evidence"]["verified"])
        self.assertTrue(result["stop_evidence"]["hint_ignored"])
        state.preflight({"reviews": [item]}, "deprecated-hint")
        feedback = state.submit({"reviews": [item]})
        self.assertTrue(state.trials[trial["trial_id"]]["selected"])
        self.assertEqual(state.format_errors, 0)
        self.assertTrue(feedback["host_stop_feedback"][0]["hint_ignored"])

    def test_previous_session_qualifying_trial_blocks_stop_until_resubmitted(self):
        payload = batch()
        previous = self.state(payload)
        trial = previous.analyze({"proposals": [edit()]})["results"][0]
        self.assertTrue(trial["verdict"]["accepted"])
        previous.submit({"reviews": [keep()]})
        payload["candidates"][0]["trial_memory"] = [{"trial_id": trial["trial_id"], "base_version": 0,
            "canonical_smiles": trial["validation"]["canonical_smiles"], "score": trial["score"],
            "verdict": trial["verdict"], "selected": False}]
        current = self.state(payload)
        current.analyze({"proposals": [edit(noop=True)]})
        incomplete = current.validate_submission({"reviews": [keep(stop=True)]})["reviews"][0]
        self.assertFalse(incomplete["stop_evidence"]["verified"])
        self.assertTrue(incomplete["stop_evidence"]["hint_ignored"])
        kept = current.validate_submission({"reviews": [keep()]})
        self.assertIn(trial["trial_id"], kept["reviews"][0]["stop_evidence"]["unsubmitted_qualifying_trial_ids"])
        self.assertFalse(kept["reviews"][0]["stop_review"])
        # An old ID is informative memory, not an authority to commit unseen operations.
        with self.assertRaisesRegex(ValueError, "Unknown or cross-candidate"):
            current.validate_submission({"reviews": [submit_edit(trial["trial_id"])]})
        refreshed = current.analyze({"proposals": [edit()]})["results"][0]
        self.assertEqual(refreshed["trial_id"], trial["trial_id"])
        selected = current.validate_submission({"reviews": [submit_edit(refreshed["trial_id"])]})
        self.assertEqual(selected["reviews"][0]["stop_evidence"]["unsubmitted_qualifying_trial_ids"], [])

    def test_pending_memory_survives_recent_history_window(self):
        payload = batch()["candidates"][0]
        payload["trial_memory"] = [
            {"trial_id": "pending-old", "base_version": 0, "verdict": {"accepted": True}, "selected": False},
            {"trial_id": "different-version", "base_version": 5, "verdict": {"accepted": True}, "selected": False},
        ] + [{"trial_id": f"rejected-{i}", "base_version": 0, "verdict": {"accepted": False}, "selected": False} for i in range(30)]
        view = compact_candidate(payload)
        ids = [t["trial_id"] for t in view["trial_memory"]]
        self.assertIn("pending-old", ids)
        self.assertNotIn("different-version", ids)
        self.assertEqual(len(ids), 21)
        self.assertEqual(ids[-1], "rejected-29")

    def test_invalid_parent_cannot_early_stop(self):
        p = batch()
        p["candidates"][0]["validation"]["valid"] = False
        state = self.state(p)
        state.analyze({"proposals": [edit(noop=True)]})
        incomplete = state.validate_submission({"reviews": [keep(stop=True)]})["reviews"][0]
        self.assertFalse(incomplete["stop_evidence"]["verified"])
        self.assertTrue(incomplete["stop_evidence"]["hint_ignored"])
        self.assertFalse(state.validate_submission({"reviews": [keep()]})["reviews"][0]["stop_review"])

    def test_operation_count_not_limited_and_trial_cap_allows_final_submission(self):
        p = batch()
        p["max_trial_batches"] = 1
        state = self.state(p)
        trial = state.analyze({"proposals": [edit(noop=True, n=12)]})["results"][0]
        self.assertEqual(trial["operation_count"], 12)
        exhausted = state.analyze({"proposals": [edit()]})
        self.assertTrue(exhausted["trial_budget_exhausted"])
        state.submit({"reviews": [submit_edit(trial["trial_id"], n=12)]})
        self.assertEqual(len(state.submitted["reviews"][0]["proposals"][0]["operations"]), 12)

    def test_incomplete_evidence_never_qualifies(self):
        state = self.state()
        record = {"validation": {"valid": True, "graph": {}, "canonical_smiles": "CC=O"},
                  "evidence": {"complete": False}, "score": {"score": .9}}
        with patch("gcms_agent.tools.batch_server.analyze_proposal", return_value=record):
            trial = state.analyze({"proposals": [edit()]})["results"][0]
        self.assertIsNone(trial["score"])
        self.assertEqual(trial["verdict"]["reason"], "evidence_incomplete")
        result = state.validate_submission({"reviews": [keep()]})["reviews"][0]
        self.assertFalse(result["stop_evidence"]["verified"])
        self.assertEqual(result["stop_evidence"]["exploration_trial_ids"], [])

    def test_format_preflight_does_not_double_count_same_tool(self):
        state = self.state()
        for _ in range(2):
            with self.assertRaises(ValueError):
                state.preflight({}, "same-call")
        self.assertEqual(state.format_errors, 1)
        audit = state.audit()
        self.assertEqual(len(audit["preflight_errors"]), 1)
        self.assertEqual([a["phase"] for a in audit["submission_attempts"]], ["preflight", "preflight_replay"])
        self.assertEqual(sum(a["counted_format_error"] for a in audit["submission_attempts"]), 1)

    def test_submission_diagnostics_capture_ref_shape_without_free_text_or_secrets(self):
        state = self.state()
        trial = state.analyze({"proposals": [edit()]})["results"][0]
        item = submit_edit(trial["trial_id"])
        item["summary"] = "private-free-text-sentinel"
        item["proposals"][0]["operation_explanations"] = ["private-explanation-sentinel"] * 3
        item["proposals"][0]["base_version"] = 0
        item["proposals"][0]["sk-synthetic-secret"] = "secret-value"
        with self.assertRaises(ValueError):
            state.preflight({"reviews": [item]}, "call-ref-shape")
        attempt = state.audit()["submission_attempts"][-1]
        self.assertFalse(attempt["accepted"])
        self.assertIn("base_version", attempt["reviews"][0]["references"][0]["fields"])
        self.assertEqual(attempt["reviews"][0]["references"][0]["expected_operation_count"], 3)
        self.assertEqual(attempt["reviews"][0]["references"][0]["operation_explanations_count"], 3)
        encoded = json.dumps(state.audit())
        for secret in ("private-free-text-sentinel", "private-explanation-sentinel", "sk-synthetic-secret", "secret-value"):
            self.assertNotIn(secret, encoded)
        self.assertIn("[REDACTED]", encoded)
        # Direct handler validation failures are also diagnosed, without relying on a hook.
        item["proposals"][0].pop("base_version")
        item["proposals"][0].pop("sk-synthetic-secret")
        item["proposals"][0]["operation_explanations"].pop()
        with self.assertRaisesRegex(ValueError, "one explanation"):
            state.submit({"reviews": [item]})
        attempt = state.audit()["submission_attempts"][-1]
        self.assertEqual(attempt["phase"], "submit")
        self.assertEqual(attempt["reviews"][0]["references"][0]["operation_explanations_count"], 2)
        self.assertEqual(state.format_errors, 2)

    def test_deprecated_hints_allow_valid_batch_round_but_never_false_completion(self):
        state = self.state(batch(("c01", "c02")))
        tid = state.analyze({"proposals": [edit()]})["results"][0]["trial_id"]
        changed = submit_edit(tid)
        changed.update(stop_review=True, checks=list(CHECKS))
        review = {"reviews": [changed, keep("c02", stop=True)]}
        state.preflight(review, "mixed-edit-keep")
        feedback = state.submit(review)
        self.assertEqual(state.format_errors, 0)
        self.assertTrue(state.trials[tid]["selected"])
        self.assertEqual(len(state.submitted["reviews"]), 2)
        self.assertTrue(all(not i["stop_evidence"]["verified"] for i in state.submitted["reviews"]))
        self.assertTrue(all(i["hint_ignored"] for i in feedback["host_stop_feedback"]))

    def test_diagnostic_replay_log_is_bounded_and_does_not_consume_format_budget(self):
        state = self.state()
        for _ in range(70):
            with self.assertRaises(ValueError):
                state.preflight({}, "one-call")
        self.assertEqual(state.format_errors, 1)
        self.assertEqual(state.audit()["submission_attempt_count"], 70)
        self.assertEqual(len(state.audit()["submission_attempts"]), 64)

    def test_tool_metrics_record_sizes_and_scoped_evidence(self):
        sdk = SimpleNamespace(tool=lambda name, description, schema: lambda handler: SimpleNamespace(name=name, handler=handler),
                              create_sdk_mcp_server=lambda **kwargs: kwargs)
        server, state, names = build_batch_server(sdk, batch(), SkillLibrary(ROOT))
        tool = next(t for t in server["tools"] if t.name == "analyze_proposals")
        asyncio.run(tool.handler({"proposals": [edit()]}))
        self.assertEqual(state.audit()["tool_metrics"][0]["tool"], "analyze_proposals")
        self.assertGreater(state.audit()["tool_metrics"][0]["output_chars"], 0)
        self.assertIn("mcp__gcms__submit_reviews", names)
        record = next(iter(state.trials.values()))
        paths = record["result"]["evidence"]["evidence"]
        if paths:
            result = state.get_evidence({"candidate_id": "c01", "trial_id": record["trial_id"], "evidence_ids": ["e1"]})
            self.assertEqual(result["evidence"]["e1"], paths[0])
        with self.assertRaisesRegex(ValueError, "Unknown evidence_id"):
            state.get_evidence({"candidate_id": "c01", "evidence_ids": ["e9999"]})
