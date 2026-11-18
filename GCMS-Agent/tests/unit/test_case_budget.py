"""Case scheduling caps remain atomic, recoverable, and independent of other cases."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from gcms_agent.workflow.budget import Budget, BudgetExhausted


class CaseBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "budget.json"
        self.config = {
            "model": {"prices": {"input": 1., "cache": 1., "output": 1.},
                      "prices_cny": {name: {"input": 1., "cache": 1., "output": 1.}
                                     for name in ("peak", "offpeak")}},
            "runtime": {"budget_currency": "USD", "budget_usd": 1., "budget_cny": 1.,
                        "max_tool_turns": 2, "max_output_tokens": 1000,
                        "reservation_input_tokens": 0, "session_timeout_seconds": 1,
                        "case_budget_usd": .003, "case_budget_cny": .003}}

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_zero_usage_connection_failures_preserve_effective_slots_and_survive_resume(self):
        self.config["runtime"].update(max_sessions_per_case=2, max_transport_failures_per_case=3,
                                      case_budget_usd=1.)
        budget = Budget.from_config(self.config, self.path)
        zero = {"known": True, "input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0}
        failure = {"error_category": "service_error", "detail": "ECONNREFUSED",
                   "audit": {"tool_metrics": [], "trials": [], "submission_attempt_count": 0}}
        for rid in ("failed1", "failed2"):
            await self.reserve(budget, rid)
            await budget.settle(rid, zero, failure=failure)
            await budget.settle(rid, zero, failure=failure)
        saved = json.loads(self.path.read_text())
        resumed = Budget.from_config(self.config, self.path, saved)
        self.assertEqual(resumed.case_usage("a")["effective_sessions"], 0)
        self.assertEqual(resumed.case_usage("a")["excluded_transport_failures"], 2)
        self.assertEqual(len(resumed.ledger["requests"]), 2)
        results = await asyncio.gather(*(self.reserve(resumed, rid) for rid in ("ok1", "ok2", "too_many")),
                                       return_exceptions=True)
        self.assertEqual(sum(isinstance(r, float) for r in results), 2)
        self.assertEqual(resumed.case_usage("a")["effective_sessions"], 2)
        self.assertEqual(resumed.spent, 0)

    async def test_excluded_failure_limit_remains_case_local(self):
        self.config["runtime"].update(max_sessions_per_case=8, max_transport_failures_per_case=1)
        budget = Budget.from_config(self.config, self.path)
        await self.reserve(budget, "failed")
        await budget.settle("failed", {"known": True, "input_tokens": 0, "output_tokens": 0,
                                       "cache_read_input_tokens": 0},
            failure={"error_category": "service_error", "detail": "ECONNREFUSED",
                     "audit": {"tool_metrics": [], "trials": [], "submission_attempt_count": 0}})
        with self.assertRaises(BudgetExhausted) as raised:
            await self.reserve(budget, "blocked")
        self.assertEqual(raised.exception.category, "case_transport_failure_limit")
        await self.reserve(budget, "other", "b")

    async def test_unknown_usage_is_not_exempt_until_explicit_receipt_reconciliation(self):
        budget = Budget.from_config(self.config, self.path)
        failure = {"error_category": "service_error", "detail": "ECONNREFUSED",
                   "audit": {"tool_metrics": [], "trials": [], "submission_attempt_count": 0}}
        await self.reserve(budget, "unknown")
        await budget.settle("unknown", {"known": False}, failure=failure)
        self.assertEqual(budget.case_usage("a")["effective_sessions"], 1)
        self.assertGreater(budget.spent, 0)
        await budget.settle("unknown", {"known": True, "input_tokens": 0, "output_tokens": 0,
                                        "cache_read_input_tokens": 0}, failure=failure)
        self.assertEqual(budget.case_usage("a")["effective_sessions"], 0)
        self.assertEqual(budget.spent, 0)

    async def reserve(self, budget, rid, sample="a"):
        return await budget.reserve_review(rid, {"sample_id": sample, "candidate_id": "c01"},
                                           self.config["runtime"])

    async def test_no_money_limits_tracks_overruns_and_unknown_usage_but_keeps_session_cap(self):
        self.config["runtime"].update(budget_usd=None, case_budget_usd=None, max_sessions_per_case=3)
        budget = Budget.from_config(self.config, self.path)
        await self.reserve(budget, "large")
        await budget.settle("large", {"known": True, "output_tokens": 10000000})
        self.assertTrue(budget.ledger["reservation_exceeded"])
        await self.reserve(budget, "unknown")
        await budget.settle("unknown", {"known": False})
        self.assertAlmostEqual(budget.spent, 10.002)
        stored = json.loads(self.path.read_text())
        self.assertIsNone(stored["limit_usd"])
        self.assertIsNone(stored["case_limit_usd"])
        self.assertFalse(stored["monetary_limits_enabled"])
        resumed = Budget.from_config(self.config, self.path, stored)
        await self.reserve(resumed, "next")
        with self.assertRaises(BudgetExhausted) as raised:
            await self.reserve(resumed, "too_many")
        self.assertEqual(raised.exception.category, "case_session_limit")

    async def test_no_output_cap_still_reserves_and_settles_real_usage(self):
        self.config["runtime"].update(max_output_tokens=None, reservation_output_tokens=2000)
        budget = Budget.from_config(self.config, self.path)
        self.assertAlmostEqual(await self.reserve(budget, "uncapped"), .002)
        await budget.settle("uncapped", {"known": True, "output_tokens": 2500})
        self.assertAlmostEqual(budget.spent, .0025)
        self.assertTrue(budget.ledger["reservation_exceeded"])

    async def test_disabled_session_timeout_reserves_across_candidate_time_window(self):
        self.config["runtime"].update(session_timeout_seconds=0, candidate_timeout_seconds=900)
        budget = Budget.from_config(self.config, self.path)
        with patch("gcms_agent.workflow.budget.interval_tariff", return_value="peak") as tariff:
            await self.reserve(budget, "no_session_deadline")
        start, end = tariff.call_args.args
        self.assertEqual((end - start).total_seconds(), 900)

    async def test_concurrent_case_cost_reservations_are_atomic_and_other_cases_continue(self):
        budget = Budget.from_config(self.config, self.path)
        snapshots = []
        save = budget.save

        def observe_save():
            save()
            snapshots.append(json.loads(self.path.read_text()))

        with patch.object(budget, "save", side_effect=observe_save):
            await self.reserve(budget, "r1")
            waiter = asyncio.create_task(self.reserve(budget, "r2"))
            await asyncio.sleep(0)
            self.assertFalse(waiter.done())
            self.assertNotIn("r2", budget.reserved)
            await self.reserve(budget, "other", "b")
            await budget.settle("r1", {"known": True, "output_tokens": 500})
            self.assertAlmostEqual(await asyncio.wait_for(waiter, 1), .002)
        for snapshot in snapshots:
            self.assertEqual(set(snapshot["pending_reservations"]), set(snapshot["reservation_metadata"]))
        self.assertAlmostEqual(budget.case_usage("a")["committed_cost"], .0025)
        self.assertAlmostEqual(budget.case_usage("b")["committed_cost"], .002)
        self.assertFalse(budget.ledger.get("reservation_exceeded", False))

    async def test_settled_and_pending_sessions_share_case_limit(self):
        self.config["runtime"].update(case_budget_usd=.1, max_sessions_per_case=2)
        budget = Budget.from_config(self.config, self.path)
        await self.reserve(budget, "settled")
        await budget.settle("settled", {"known": True, "output_tokens": 1})
        outcomes = await asyncio.gather(*(self.reserve(budget, rid) for rid in ("r1", "r2")),
                                        return_exceptions=True)
        self.assertEqual(sum(isinstance(result, float) for result in outcomes), 1)
        rejected = next(result for result in outcomes if isinstance(result, BudgetExhausted))
        self.assertEqual(rejected.category, "case_session_limit")
        self.assertEqual(budget.case_usage("a")["committed_sessions"], 2)
        await self.reserve(budget, "other", "b")

    async def test_recovered_unknown_charge_blocks_case_until_real_receipt_reconciles(self):
        budget = Budget.from_config(self.config, self.path)
        await self.reserve(budget, "lost")
        recovered = Budget.from_config(self.config, self.path, json.loads(self.path.read_text()))
        current = recovered.case_usage("a")
        self.assertEqual(current["unknown_usage_sessions"], 1)
        self.assertEqual(current["committed_sessions"], 1)
        self.assertAlmostEqual(current["committed_cost"], .002)
        with self.assertRaises(BudgetExhausted) as raised:
            await self.reserve(recovered, "blocked")
        self.assertEqual(raised.exception.category, "case_budget_exhausted")
        await self.reserve(recovered, "other", "b")
        actual = {"known": True, "output_tokens": 500}
        await recovered.settle("lost", actual)
        await recovered.settle("lost", actual)
        await self.reserve(recovered, "after_receipt")
        current = recovered.case_usage("a")
        self.assertAlmostEqual(current["committed_cost"], .0025)
        self.assertEqual(current["committed_sessions"], 2)
        self.assertEqual(current["unknown_usage_sessions"], 0)

    async def test_idempotent_reservation_preserves_scope_and_does_not_take_another_slot(self):
        self.config["runtime"]["max_sessions_per_case"] = 1
        budget = Budget.from_config(self.config, self.path)
        await self.reserve(budget, "r")
        metadata = deepcopy(budget.reservation_metadata["r"])
        await self.reserve(budget, "r")
        self.assertEqual(budget.reservation_metadata["r"], metadata)
        self.assertEqual(budget.case_usage("a")["committed_sessions"], 1)
        with self.assertRaisesRegex(ValueError, "scope"):
            await self.reserve(budget, "r", "b")
        self.assertEqual(budget.case_usage("b")["committed_sessions"], 0)

    async def test_case_cap_uses_ledger_currency_and_global_cap_is_still_enforced(self):
        self.config["runtime"].update(budget_currency="CNY", case_budget_usd=.00001,
                                      budget_cny=.003)
        budget = Budget.from_config(self.config, self.path)
        await self.reserve(budget, "a", "a")
        await budget.settle("a", {"known": True, "output_tokens": 2000})
        with self.assertRaises(BudgetExhausted) as raised:
            await self.reserve(budget, "b", "b")
        self.assertEqual(raised.exception.category, "budget_exhausted")
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved["case_limit_cny"], .003)
        self.assertNotIn("case_limit_usd", saved)

    async def test_global_pending_budget_waits_then_wakes_on_settlement(self):
        self.config["runtime"]["budget_usd"] = .003
        budget = Budget.from_config(self.config, self.path)
        await self.reserve(budget, "a", "a")
        waiter = asyncio.create_task(self.reserve(budget, "b", "b"))
        await asyncio.sleep(0)
        self.assertFalse(waiter.done())
        await budget.settle("a", {"known": True, "output_tokens": 500})
        self.assertAlmostEqual(await asyncio.wait_for(waiter, 1), .002)

    async def test_waiter_fails_locally_if_actual_settlement_leaves_too_little_case_budget(self):
        budget = Budget.from_config(self.config, self.path)
        await self.reserve(budget, "a")
        waiter = asyncio.create_task(self.reserve(budget, "b"))
        await asyncio.sleep(0)
        self.assertFalse(waiter.done())
        await budget.settle("a", {"known": False})
        with self.assertRaises(BudgetExhausted) as raised:
            await asyncio.wait_for(waiter, 1)
        self.assertEqual(raised.exception.category, "case_budget_exhausted")
        await self.reserve(budget, "other", "other")

    async def test_cancelled_waiter_does_not_leave_a_phantom_charge(self):
        budget = Budget.from_config(self.config, self.path)
        await self.reserve(budget, "a")
        waiter = asyncio.create_task(self.reserve(budget, "b"))
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertEqual(budget.case_usage("a")["committed_sessions"], 1)
        self.assertEqual(set(budget.reservation_metadata), {"a"})

    async def test_release_unsent_request_wakes_waiter_without_recording_a_paid_session(self):
        budget = Budget.from_config(self.config, self.path)
        await self.reserve(budget, "unsent")
        waiter = asyncio.create_task(self.reserve(budget, "waiting"))
        await asyncio.sleep(0)
        self.assertFalse(waiter.done())
        self.assertTrue(await budget.release("unsent"))
        self.assertFalse(await budget.release("unsent"))
        self.assertAlmostEqual(await asyncio.wait_for(waiter, 1), .002)
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved["spent_usd"], 0)
        self.assertEqual(saved["requests"], [])
        self.assertEqual(saved["sessions"], 0)
        self.assertEqual(set(saved["pending_reservations"]), {"waiting"})
        self.assertEqual(budget.case_usage("a")["committed_sessions"], 1)

    async def test_release_cannot_erase_known_or_unknown_settled_usage(self):
        budget = Budget.from_config(self.config, self.path)
        for rid, usage in (("known", {"known": True, "output_tokens": 500}),
                           ("unknown", {"known": False})):
            await self.reserve(budget, rid, rid)
            await budget.settle(rid, usage)
            before = deepcopy(budget.ledger)
            with self.assertRaisesRegex(ValueError, "settled"):
                await budget.release(rid)
            self.assertEqual(budget.ledger, before)

    async def test_failed_reservation_persistence_does_not_leave_an_unowned_pending_charge(self):
        budget = Budget.from_config(self.config, self.path)
        with patch.object(budget, "save", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                await self.reserve(budget, "never_sent")
        self.assertFalse(budget.reserved)
        self.assertFalse(budget.reservation_metadata)
        self.assertEqual(budget.case_usage("a")["committed_sessions"], 0)
        await self.reserve(budget, "next")

    async def test_settlement_persistence_error_still_wakes_waiter(self):
        budget = Budget.from_config(self.config, self.path)
        await self.reserve(budget, "first")
        waiter = asyncio.create_task(self.reserve(budget, "waiting"))
        await asyncio.sleep(0)
        self.assertFalse(waiter.done())
        with patch.object(budget, "save", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                await budget.settle("first", {"known": True, "output_tokens": 500})
        self.assertAlmostEqual(await asyncio.wait_for(waiter, 1), .002)

    async def test_stream_guard_does_not_reduce_cost_reservation(self):
        self.config["runtime"]["session_output_token_limit"] = 1
        budget = Budget.from_config(self.config, self.path)
        amount = await self.reserve(budget, "r")
        self.assertAlmostEqual(amount, .002)
        self.assertEqual(budget.reservation_metadata["r"]["reservation_usage"]["output_tokens"], 2000)

    async def test_case_scoped_review_requires_sample_identity(self):
        budget = Budget.from_config(self.config, self.path)
        with self.assertRaisesRegex(ValueError, "sample_id"):
            await budget.reserve_review("r", {}, self.config["runtime"])
        self.assertFalse(budget.reserved)

    async def test_invalid_optional_case_limits_rejected_before_any_charge(self):
        for value in (0, -1, True, float("nan"), float("inf"), "1"):
            config = deepcopy(self.config)
            config["runtime"]["case_budget_usd"] = value
            with self.subTest(case_budget=value), self.assertRaises(ValueError):
                Budget.from_config(config, self.path)
        for value in (0, -1, True, 1.5, "2"):
            config = deepcopy(self.config)
            config["runtime"]["max_sessions_per_case"] = value
            with self.subTest(max_sessions=value), self.assertRaises(ValueError):
                Budget.from_config(config, self.path)


if __name__ == "__main__":
    unittest.main()
