"""Synthetic end-to-end checks for batching, acceptance and exactly-once recovery."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gcms_agent.config import digest, load_config, write_json
from gcms_agent.chemistry.smiles_io import parse_smiles
from gcms_agent.workflow.candidate import assess
from gcms_agent.workflow.runner import run_batch
from gcms_agent.workflow.checkpoint import Checkpoints


CHECKS = {name: True for name in ("graph_read", "valence_checked", "spectrum_checked", "alternatives_checked")}


class BatchReviewer:
    def __init__(self, *, stop=False, repair=False, partial=False):
        self.calls = []
        self.stop, self.repair, self.partial = stop, repair, partial

    async def review_batch(self, payload):
        self.calls.append(copy.deepcopy({k: v for k, v in payload.items() if k != "trial_cache"}))
        reviews, trials = [], []
        for candidate in payload["candidates"]:
            # Synthetic tool receipt with a real legal alternative. These
            # tests exercise orchestration; chemical transactions are covered
            # separately through the actual BatchReviewTools below.
            alternative = "C=C" if candidate["validation"].get("canonical_smiles") == "CC" else "CC"
            validation, evidence, score = assess(parse_smiles(alternative)["graph"], payload["peaks"])
            trial_id = "synthetic-trial-" + candidate["candidate_id"]
            trials.append({"candidate_id": candidate["candidate_id"], "trial_id": trial_id,
                           "base_version": candidate["graph_version"], "graph_hash": digest(candidate["graph"]),
                           "rules_version": payload["rules_version"], "spectrum_hash": digest(payload["peaks"]),
                           "result": {"validation": validation, "evidence": evidence, "score": score},
                           "verdict": {"accepted": False, "reason": "synthetic_rejected_alternative"}, "selected": False})
            proposals = []
            if self.repair and candidate["round"] == 1 and not candidate["validation"]["valid"]:
                graph = candidate["graph"]
                operations = [{"op": "set_bond", "a": b["a"], "b": b["b"], "order": 1} for b in graph["bonds"]]
                operations += [{"op": "set_atom", "id": a["id"], "hydrogens": 2, "radicals": 0} for a in graph["atoms"]]
                proposals = [{"base_version": candidate["graph_version"], "operations": operations,
                              "reason": "Synthetic validity repair", "peak_refs": [],
                              "operation_explanations": ["Companion valence repair; no spectral support"] * len(operations)}]
            reviews.append({"candidate_id": candidate["candidate_id"], "decision": "edit" if proposals else "keep",
                            "proposals": proposals, "summary": "Synthetic complete review", "stop_review": self.stop,
                            "stop_evidence": {"verified": self.stop, "checks": CHECKS, "trial_ids": [trial_id],
                                              "unsubmitted_qualifying_trial_ids": []}})
        if self.partial:
            reviews = reviews[:-1]
        return {"reviews": reviews, "format_errors": 0, "actual_models": ["synthetic"],
                "audit": {"trials": trials, "tool_metrics": []},
                "usage": {"known": True, "input_tokens": 100, "cache_read_input_tokens": 20, "output_tokens": 10}}


class CaseBatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config = load_config()
        self.config["runtime"].update(review_strategy="case_batch", budget_currency="CNY", budget_cny=30,
                                      batch_size=4, max_batch_chars=20000, offpeak_only=False)
        self.row = {"sample_id": "synthetic", "input_ids": [499], "intensity": [29],
                    "generated": ["CC"] * 5 + ["C=C"] * 5}

    def tearDown(self):
        self.temporary.cleanup()

    async def test_two_connection_failures_then_full_reviews_and_idempotent_resume(self):
        from gcms_agent.harness.client import HarnessError, classify_failure
        class RefusedTwice(BatchReviewer):
            failures = 0
            async def review_batch(inner, payload):
                if inner.failures < 2:
                    inner.failures += 1
                    category, retryable = classify_failure(None, "ECONNREFUSED")
                    raise HarnessError("connection failed", category=category, retryable=retryable,
                        detail="ECONNREFUSED", usage={"known": True, "input_tokens": 0,
                        "cache_read_input_tokens": 0, "output_tokens": 0},
                        audit={"trials": [], "tool_metrics": [], "submission_attempt_count": 0})
                return await super().review_batch(payload)
        self.config["runtime"].update(batch_size=1, batch_concurrency=1, max_sessions_per_case=2)
        reviewer = RefusedTwice(stop=True)
        summary = await self.run_case(reviewer)
        self.assertEqual(summary["complete_samples"], 1)
        usage = self.result()["resource_usage"]
        self.assertEqual((usage["sessions"], usage["effective_sessions"], usage["excluded_transport_failures"]), (4, 2, 2))
        before = len(reviewer.calls)
        await self.run_case(reviewer, resume=True)
        self.assertEqual(len(reviewer.calls), before)

    async def test_prior_wave_exploration_enters_next_batch_and_checkpoint(self):
        from gcms_agent.harness.prompts import batch_inference_payload
        from gcms_agent.harness.compact import compact_batch
        self.config["runtime"].update(batch_size=1, batch_concurrency=1)
        reviewer = BatchReviewer(stop=True)
        await self.run_case(reviewer)
        shared = compact_batch(batch_inference_payload(reviewer.calls[1]))["case_exploration"]
        self.assertTrue(any(s["trial_count"] > 0 for s in shared["structures"]))
        self.assertNotIn('trial_ids', json.dumps(shared))
        checkpoint = json.loads((self.root / 'run/checkpoints/synthetic__case.json').read_text(encoding='utf-8'))
        self.assertIn('exploration_archive', checkpoint)

    async def run_case(self, reviewer, *, resume=False):
        source = self.root / "input.json"
        if not source.exists():
            write_json(source, [self.row])
        return await run_batch(source, self.root / "run", self.config, client=reviewer, resume=resume)

    def result(self):
        return json.loads((self.root / "run/results/synthetic.json").read_text(encoding="utf-8"))

    def ledger(self):
        return json.loads((self.root / "run/logs/budget.json").read_text(encoding="utf-8"))

    async def test_unlimited_money_allows_followup_after_reservation_overrun(self):
        self.config["runtime"].update(budget_cny=None, case_budget_cny=None,
                                      reservation_input_tokens=0, max_output_tokens=None,
                                      reservation_output_tokens=1)
        reviewer = BatchReviewer()
        summary = await self.run_case(reviewer)
        self.assertEqual(len(reviewer.calls), 2)
        self.assertEqual(summary["complete_samples"], 1)
        ledger = self.ledger()
        self.assertTrue(ledger["reservation_exceeded"])
        self.assertFalse(ledger["monetary_limits_enabled"])
        self.assertEqual(ledger["sessions"], 2)
        self.assertEqual(ledger["unknown_usage_sessions"], 0)
        self.assertAlmostEqual(ledger["spent_cny"], sum(r["cost_cny"] for r in ledger["requests"]))

    async def test_two_candidates_share_sessions_and_bill_once(self):
        reviewer = BatchReviewer()
        summary = await self.run_case(reviewer)
        self.assertEqual(len(reviewer.calls), 2)

        self.assertTrue(all(len(call["candidates"]) == 2 for call in reviewer.calls))
        self.assertTrue(all("peaks" not in candidate for call in reviewer.calls for candidate in call["candidates"]))
        self.assertEqual(summary["complete_samples"], 1)
        self.assertEqual(len(self.result()["ranked_candidates"]), 10)
        self.assertEqual(len(self.ledger()["requests"]), 2)
        self.assertEqual(self.ledger()["usage"]["output_tokens"], 20)
        self.assertEqual(self.result()["resource_usage"]["output_tokens"], 20)
        self.assertEqual(self.result()["resource_usage"]["sessions"], 2)
        for candidate in self.result()["ranked_candidates"]:
            self.assertTrue(all("usage" not in entry for entry in candidate["history"]))
        await self.run_case(reviewer, resume=True)
        self.assertEqual(len(reviewer.calls), 2)

    async def test_resume_rejects_switching_mock_to_sdk_backend(self):
        await self.run_case(BatchReviewer())
        manifest = json.loads((self.root / "run/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["backend"], "injected_client")
        with self.assertRaisesRegex(ValueError, "backend"):
            await run_batch(self.root / "input.json", self.root / "run", self.config, resume=True)

    async def test_checked_keep_can_end_one_round_but_invalid_keep_cannot(self):
        self.row["generated"] = ["CC"] * 5 + ["c1cccc1"] * 5
        reviewer = BatchReviewer(stop=True)
        await self.run_case(reviewer)
        self.assertEqual([len(call["candidates"]) for call in reviewer.calls], [2, 1])
        valid, invalid = self.result()["ranked_candidates"][0], self.result()["ranked_candidates"][-1]
        by_original = {r["original_smiles"]: r for r in self.result()["ranked_candidates"]}
        self.assertEqual(by_original["CC"]["stop_reason"], "review_complete")
        self.assertEqual(by_original["c1cccc1"]["stop_reason"], "stagnation")
        self.assertIsNone(by_original["c1cccc1"]["final_smiles"])

    async def test_stop_boolean_without_checked_evidence_does_not_end_search(self):
        class Unchecked(BatchReviewer):
            async def review_batch(self, payload):
                response = await super().review_batch(payload)
                for entry in response["reviews"]:
                    entry.pop("stop_evidence")
                return response
        reviewer = Unchecked(stop=True)
        await self.run_case(reviewer)
        self.assertEqual(len(reviewer.calls), self.config["runtime"]["max_rounds"])
        self.assertTrue(all(r["stop_reason"] == "max_rounds" for r in self.result()["ranked_candidates"]))

    async def test_host_uses_verified_evidence_without_model_stop_hint(self):
        class VerifiedWithoutHint(BatchReviewer):
            async def review_batch(self, payload):
                response = await super().review_batch(payload)
                for entry in response["reviews"]:
                    entry["stop_review"] = False
                return response
        reviewer = VerifiedWithoutHint(stop=True)
        await self.run_case(reviewer)
        self.assertEqual(len(reviewer.calls), 1)
        self.assertTrue(all(r["stop_reason"] == "review_complete" for r in self.result()["ranked_candidates"]))

    async def test_host_rechecks_pending_trials_before_accepting_stop(self):
        class PendingDespiteStop(BatchReviewer):
            async def review_batch(self, payload):
                response = await super().review_batch(payload)
                response["audit"]["trials"] = [
                    {"candidate_id": c["candidate_id"], "trial_id": "pending-" + c["candidate_id"],
                     "base_version": c["graph_version"], "result": {},
                     "verdict": {"accepted": True}, "selected": False}
                    for c in payload["candidates"]]
                return response
        reviewer = PendingDespiteStop(stop=True)
        await self.run_case(reviewer)
        self.assertEqual(len(reviewer.calls), self.config["runtime"]["max_rounds"])
        self.assertTrue(all(r["stop_reason"] == "max_rounds" for r in self.result()["ranked_candidates"]))

    async def test_unaudited_invalid_or_incomplete_attempts_cannot_stop_or_stagnate(self):
        for mode in ("zero", "invalid", "incomplete", "noop", "stale"):
            with self.subTest(mode=mode):
                class Insufficient(BatchReviewer):
                    async def review_batch(inner, payload):
                        response = await super(Insufficient, inner).review_batch(payload)
                        if mode == "zero":
                            response["audit"]["trials"] = []
                        for trial, candidate in zip(response["audit"]["trials"], payload["candidates"]):
                            if mode == "invalid":
                                trial["result"]["validation"]["valid"] = False
                            if mode == "incomplete":
                                trial["result"]["evidence"]["complete"] = False
                            if mode == "noop":
                                trial["result"]["validation"]["canonical_smiles"] = candidate["validation"]["canonical_smiles"]
                            if mode == "stale":
                                trial["graph_hash"] = "previous-graph"
                        return response
                # Separate destinations keep each independent test isolated.
                saved_root = self.root
                self.root = saved_root / mode
                self.root.mkdir()
                reviewer = Insufficient(stop=True)
                try:
                    await self.run_case(reviewer)
                    self.assertEqual(len(reviewer.calls), self.config["runtime"]["max_rounds"])
                    for row in self.result()["ranked_candidates"]:
                        self.assertEqual(row["stop_reason"], "max_rounds")
                        self.assertEqual(row["stagnation_count"], 0)
                        self.assertTrue(all(h["exploration_feedback"]["status"] == "exploration_insufficient" for h in row["history"]))
                    from gcms_agent.harness.compact import compact_candidate
                    candidate = compact_candidate(reviewer.calls[-1]["candidates"][0])
                    self.assertEqual(candidate["previous_feedback"][-1]["exploration_feedback"]["status"], "exploration_insufficient")
                finally:
                    self.root = saved_root

    async def test_ten_operation_repair_remains_valid_without_supported_peaks(self):
        self.row["generated"] = ["c1cccc1"] * 10
        reviewer = BatchReviewer(repair=True, stop=True)
        await self.run_case(reviewer)
        candidate = self.result()["ranked_candidates"][0]
        self.assertEqual(candidate["final_smiles"], "C1CCCC1")
        self.assertEqual(candidate["score"]["score"], 0)
        self.assertEqual(candidate["accepted_edits"], 1)
        self.assertEqual(len(candidate["history"][0]["outcomes"][0]["proposal"]["operations"]), 10)
        self.assertEqual(len(reviewer.calls), 2)

    async def test_partial_batch_is_not_marked_complete(self):
        reviewer = BatchReviewer(partial=True)
        summary = await self.run_case(reviewer)
        self.assertEqual(len(reviewer.calls), 1)
        self.assertEqual(summary["complete_samples"], 0)
        self.assertFalse(self.result()["ranking_complete"])
        self.assertTrue(all(r["stop_reason"] == "missing_submission" for r in self.result()["ranked_candidates"]))

    async def test_budget_blocks_whole_batch_before_provider_call(self):
        self.config["runtime"]["budget_cny"] = 1e-10
        reviewer = BatchReviewer()
        summary = await self.run_case(reviewer)
        self.assertEqual(reviewer.calls, [])
        self.assertEqual(summary["spent_cny"], 0)
        self.assertFalse(self.result()["ranking_complete"])

    async def test_initial_review_covers_all_candidates_before_next_round(self):
        self.row["generated"] = ["C", "CC", "CCC", "CCCC", "CCO", "CO", "CCN", "CN", "CCOC", "CCCO"]
        reviewer = BatchReviewer()
        await self.run_case(reviewer)
        self.assertEqual([len(call["candidates"]) for call in reviewer.calls], [4, 4, 2, 4, 4, 2])
        self.assertTrue(all(c["round"] == 1 for call in reviewer.calls[:3] for c in call["candidates"]))

    async def test_receipt_replays_atomic_batch_without_duplicate_payment(self):
        reviewer = BatchReviewer()
        save = Checkpoints.save
        def crash_on_commit(instance, key, value):
            if key == "synthetic__case" and value.get("pending") is None and any(
                    state.get("rounds_completed", 0) > 0 for state in value.get("states", {}).values()):
                raise RuntimeError("synthetic disk failure after receipt")
            return save(instance, key, value)
        with patch.object(Checkpoints, "save", crash_on_commit):
            with self.assertRaisesRegex(RuntimeError, "disk failure"):
                await self.run_case(reviewer)
        self.assertEqual(len(reviewer.calls), 1)
        resumed = BatchReviewer()
        summary = await self.run_case(resumed, resume=True)
        self.assertEqual(len(resumed.calls), 1)
        self.assertEqual(summary["complete_samples"], 1)
        self.assertEqual(len(self.ledger()["requests"]), 2)
        self.assertTrue(all(len(r["history"]) == 2 for r in self.result()["ranked_candidates"]))

    async def test_real_tool_submission_stop_contract_and_memory(self):
        from gcms_agent.tools.batch_server import BatchReviewTools, CHECKS as TOOL_CHECKS
        from gcms_agent.harness.skill_loader import SkillLibrary
        from gcms_agent.config import PROJECT_ROOT
        class ActualTools(BatchReviewer):
            async def review_batch(self, payload):
                self.calls.append(payload)
                state = BatchReviewTools(payload, SkillLibrary(PROJECT_ROOT))
                proposals = []
                for candidate in payload["candidates"]:
                    graph = candidate["graph"]
                    bond = graph["bonds"][0]
                    order = 2 if bond["order"] == 1 else 1
                    operations = [{"op": "set_bond", "a": bond["a"], "b": bond["b"], "order": order}]
                    operations += [{"op": "set_atom", "id": atom["id"], "hydrogens": 4 - order}
                                   for atom in graph["atoms"]]
                    proposals.append({"candidate_id": candidate["candidate_id"], "base_version": candidate["graph_version"],
                                      "operations": operations})
                state.analyze({"proposals": proposals})
                state.submit({"reviews": [{"candidate_id": candidate["candidate_id"], "decision": "keep", "proposals": [],
                                           "summary": "No new supported evidence", "stop_review": False,
                                           "checks": list(TOOL_CHECKS)} for candidate in payload["candidates"]]})
                return {**state.submitted, "audit": state.audit(), "format_errors": state.format_errors,
                        "usage": {"known": True, "input_tokens": 100, "output_tokens": 10}}
        reviewer = ActualTools()
        await self.run_case(reviewer)
        self.assertEqual(len(reviewer.calls), 1)
        self.assertTrue(all(r["stop_reason"] == "review_complete" for r in self.result()["ranked_candidates"]))
        self.assertTrue(all(len(r["trial_memory"]) == 1 for r in self.result()["ranked_candidates"]))

    async def test_failed_session_audit_is_saved_without_success_receipt(self):
        from gcms_agent.harness.client import HarnessError
        class Failed(BatchReviewer):
            async def review_batch(self, payload):
                error = HarnessError("turn cap", category="turn_limit", usage={"known": True, "input_tokens": 10, "output_tokens": 5})
                error.audit = {"trials": [], "tool_metrics": [{"name": "analyze_proposals", "characters": 123}]}
                raise error
        summary = await self.run_case(Failed())
        self.assertEqual(summary["complete_samples"], 0)
        failures = list((self.root / "run/checkpoints").glob("*_failure.json"))
        self.assertEqual(len(failures), 1)
        failure = json.loads(failures[0].read_text(encoding="utf-8"))
        self.assertEqual(failure["audit"]["tool_metrics"][0]["characters"], 123)
        self.assertEqual(list((self.root / "run/checkpoints").glob("*_review.json")), [])

    def test_memory_keeps_all_current_pending_trials_plus_twenty_others(self):
        from gcms_agent.workflow.case import initialize_candidate, candidate_payload, remember_trials
        from gcms_agent.data.intensity import raw_peaks
        state = initialize_candidate("c01", "CC", raw_peaks([499], [29]), {})
        state["graph_version"] = 2
        trials = []
        for index in range(30):
            trials.append({"candidate_id": "c01", "trial_id": f"pending-{index}", "base_version": 2,
                           "result": {}, "verdict": {"accepted": True}, "selected": False})
        for index in range(30):
            trials.append({"candidate_id": "c01", "trial_id": f"old-{index}", "base_version": 1,
                           "result": {}, "verdict": {"accepted": True}, "selected": False})
        remember_trials({"c01": state}, {"trials": trials})
        self.assertEqual(len(state["trial_memory"]), 50)
        memory = candidate_payload(state, self.config["runtime"])["trial_memory"]
        self.assertEqual({entry["trial_id"] for entry in memory if entry["base_version"] == 2},
                         {f"pending-{index}" for index in range(30)})
        self.assertEqual({entry["trial_id"] for entry in memory if entry["base_version"] == 1},
                         {f"old-{index}" for index in range(10, 30)})
        # Once the graph changes, those pending records become ordinary history.
        state["graph_version"] = 3
        self.assertEqual(len(candidate_payload(state, self.config["runtime"])["trial_memory"]), 20)

    async def test_output_limit_keeps_failure_and_continues_other_groups_and_cases(self):
        from gcms_agent.harness.client import HarnessError
        class TruncatedOnce(BatchReviewer):
            def __init__(inner):
                super().__init__(stop=True)
                inner.failed = False
            async def review_batch(inner, payload):
                if not inner.failed:
                    inner.failed = True
                    inner.calls.append(copy.deepcopy({k: v for k, v in payload.items() if k != "trial_cache"}))
                    raise HarnessError("truncated", category="output_limit", retryable=False,
                        detail="API Error: response exceeded the 8192 output token maximum. sk-synthetic-private",
                        usage={"known": True, "input_tokens": 100, "output_tokens": 8192},
                        audit={"terminal_metadata": {"stop_reason": "max_tokens"}})
                return await super().review_batch(payload)
        self.config["runtime"]["batch_size"] = 2
        self.row["generated"] = ["C", "CC", "CCC", "CCCC"] + ["CC"] * 6
        second = {**self.row, "sample_id": "synthetic_second", "generated": ["CC"] * 10}
        source = self.root / "input.json"
        write_json(source, [self.row, second])
        reviewer = TruncatedOnce()
        summary = await run_batch(source, self.root / "run", self.config, client=reviewer)
        self.assertEqual(summary["processed_samples"], 2)
        self.assertEqual(summary["complete_samples"], 1)
        first = self.result()
        self.assertFalse(first["ranking_complete"])
        reasons = {c["computed_candidate_id"]: c["stop_reason"] for c in first["ranked_candidates"]}
        self.assertEqual(reasons, {"c01": "output_limit", "c02": "output_limit", "c03": "review_complete", "c04": "review_complete"})
        failure_paths = list((self.root / "run/checkpoints").glob("*_failure.json"))
        self.assertEqual(len(failure_paths), 1)
        failure = json.loads(failure_paths[0].read_text(encoding="utf-8"))
        self.assertNotIn("sk-synthetic-private", failure["detail"])
        self.assertEqual(failure["audit"]["terminal_metadata"]["stop_reason"], "max_tokens")
        self.assertEqual(self.ledger()["sessions"], 3)
        self.assertEqual(self.ledger()["usage"]["output_tokens"], 8212)
        calls = len(reviewer.calls)
        await run_batch(source, self.root / "run", self.config, client=reviewer, resume=True)
        self.assertEqual(len(reviewer.calls), calls)  # A deterministic truncation is not silently resampled.


if __name__ == "__main__":
    unittest.main()
