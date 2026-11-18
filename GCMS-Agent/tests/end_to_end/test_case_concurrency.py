"""Parallel batch scheduling with durable receipts, barriers and real billing."""
import asyncio
import copy
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from gcms_agent.config import digest, load_config
from gcms_agent.data.intensity import raw_peaks
from gcms_agent.harness.client import HarnessError
from gcms_agent.workflow.budget import Budget
from gcms_agent.workflow.case import initialize_candidate, process_case
from gcms_agent.workflow.checkpoint import Checkpoints


USAGE = {"known": True, "input_tokens": 100, "cache_read_input_tokens": 20, "output_tokens": 10}


class ConcurrentReviewer:
    def __init__(self, delay=.06, *, repair=False, fail=None):
        self.delay, self.repair, self.fail = delay, repair, fail
        self.calls, self.events = [], []
        self.active = self.maximum_active = 0

    async def review_batch(self, payload):
        candidates = payload["candidates"]
        cid, number = candidates[0]["candidate_id"], candidates[0]["round"]
        self.calls.append((cid, number))
        self.events.append(("start", cid, number))
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        try:
            await asyncio.sleep(self.delay(cid) if callable(self.delay) else self.delay)
            if cid == self.fail:
                raise HarnessError("synthetic provider failure", category="billing_error", usage=USAGE)
            reviews = []
            for candidate in candidates:
                proposals = []
                if self.repair and not candidate["validation"]["valid"]:
                    graph = candidate["graph"]
                    operations = [{"op": "set_bond", "a": b["a"], "b": b["b"], "order": 1}
                                  for b in graph["bonds"]]
                    operations += [{"op": "set_atom", "id": a["id"], "hydrogens": 2, "radicals": 0}
                                   for a in graph["atoms"]]
                    proposals = [{"base_version": candidate["graph_version"], "operations": operations,
                                  "reason": "Restore a valid carbon ring", "peak_refs": [],
                                  "operation_explanations": ["Restore valence"] * len(operations)}]
                reviews.append({"candidate_id": candidate["candidate_id"],
                                "decision": "edit" if proposals else "keep", "proposals": proposals,
                                "summary": "Synthetic review"})
            return {"reviews": reviews, "usage": dict(USAGE), "format_errors": 0, "audit": {}}
        finally:
            self.active -= 1
            self.events.append(("end", cid, number))


class CaseConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = load_config()
        self.config["runtime"].update(batch_size=1, batch_concurrency=2, max_rounds=1,
                                      offpeak_only=False, budget_currency="USD", budget_usd=100)
        self.row = {"sample_id": "parallel"}
        self.jobs = [(f"c{i:02d}", smiles) for i, smiles in enumerate(("CC", "CCC", "CO", "CN"), 1)]
        self.peaks = raw_peaks([499], [29])

    def tearDown(self):
        self.temp.cleanup()

    def resources(self, name="run", existing=None):
        root = self.root / name
        checkpoints = Checkpoints(root / "checkpoints")
        budget = Budget(100, {"input": 1, "cache": 1, "output": 1}, root / "budget.json", existing)
        return checkpoints, budget

    async def execute(self, reviewer, checkpoints, budget, *, config=None, jobs=None, control=None):
        return await process_case(self.row, jobs or self.jobs, self.peaks, config or self.config,
                                  reviewer, budget, checkpoints, control)

    async def test_parallel_waves_reduce_wall_time_and_bill_each_batch_once(self):
        timings, states_by_width = {}, {}
        for width in (1, 2):
            config = copy.deepcopy(self.config)
            config["runtime"]["batch_concurrency"] = width
            checkpoints, budget = self.resources(str(width))
            reviewer = ConcurrentReviewer(.10)
            started = time.monotonic()
            states_by_width[width] = await self.execute(reviewer, checkpoints, budget, config=config)
            timings[width] = time.monotonic() - started
            self.assertEqual(reviewer.maximum_active, width)
            self.assertEqual(len(budget.ledger["requests"]), 4)
            self.assertEqual(len({r["request_id"] for r in budget.ledger["requests"]}), 4)
            self.assertAlmostEqual(budget.spent, 4 * budget.cost(USAGE))
            self.assertFalse(budget.reserved)
        self.assertLess(timings[2], timings[1] * .85)
        for width in (1, 2):
            self.assertTrue(all(s["rounds_completed"] == 1 and s["stop_reason"] == "max_rounds"
                                for s in states_by_width[width]))

    async def test_round_barrier_waits_for_all_first_reviews(self):
        self.config["runtime"]["max_rounds"] = 2
        reviewer = ConcurrentReviewer(lambda cid: .05 if cid == "c02" else .005)
        checkpoints, budget = self.resources()
        await self.execute(reviewer, checkpoints, budget, jobs=self.jobs[:3])
        first_second_start = next(i for i, event in enumerate(reviewer.events) if event[0] == "start" and event[2] == 2)
        first_round_ends = [i for i, event in enumerate(reviewer.events) if event[0] == "end" and event[2] == 1]
        self.assertEqual(len(first_round_ends), 3)
        self.assertLess(max(first_round_ends), first_second_start)
        self.assertEqual(budget.ledger["sessions"], 6)

    async def test_wave_commit_crash_replays_edits_and_cost_without_model_calls(self):
        jobs = [("c01", "c1cccc1"), ("c02", "c1cccc1")]
        reviewer = ConcurrentReviewer(.01, repair=True)
        checkpoints, budget = self.resources()
        original_save = checkpoints.save

        def crash_on_commit(key, value):
            if key == "parallel__case" and value.get("pending") is None and any(
                    state.get("rounds_completed", 0) for state in value.get("states", {}).values()):
                raise OSError("synthetic atomic commit failure")
            original_save(key, value)

        with patch.object(checkpoints, "save", crash_on_commit):
            with self.assertRaisesRegex(OSError, "atomic commit"):
                await self.execute(reviewer, checkpoints, budget, jobs=jobs)
        self.assertEqual(reviewer.active, 0)
        self.assertEqual(budget.ledger["sessions"], 2)
        self.assertEqual(len(checkpoints.load("parallel__case")["pending"]["batches"]), 2)
        resumed = ConcurrentReviewer(repair=True)
        states = await self.execute(resumed, checkpoints, budget, jobs=jobs)
        self.assertEqual(resumed.calls, [])
        self.assertEqual(budget.ledger["sessions"], 2)
        self.assertAlmostEqual(budget.spent, 2 * budget.cost(USAGE))
        self.assertTrue(all(s["accepted_edits"] == s["graph_version"] == 1 for s in states))
        self.assertTrue(all(s["final_smiles"] == "C1CCCC1" and len(s["history"]) == 1 for s in states))

    async def test_fatal_failure_drains_inflight_and_prevents_next_wave(self):
        reviewer = ConcurrentReviewer(lambda cid: .005 if cid == "c01" else .06, fail="c01")
        checkpoints, budget = self.resources()
        control = {"fatal_reason": None}
        states = await self.execute(reviewer, checkpoints, budget, control=control)
        self.assertEqual(reviewer.calls, [("c01", 1), ("c02", 1)])
        self.assertEqual(reviewer.active, 0)
        self.assertEqual(control["fatal_reason"], "billing_error")
        self.assertEqual(budget.ledger["sessions"], 2)
        self.assertAlmostEqual(budget.spent, 2 * budget.cost(USAGE))
        self.assertEqual(states[1]["rounds_completed"], 1)
        self.assertTrue(all(s["stop_reason"] == "billing_error" for s in states))

    async def test_fatal_settlement_wakes_waiter_without_starting_another_request(self):
        self.config["runtime"].update(reservation_input_tokens=1000, max_output_tokens=100, max_tool_turns=2)
        checkpoints, budget = self.resources()
        budget.limit = .0015  # One .0012 reservation fits, two do not.
        reviewer = ConcurrentReviewer(.02, fail="c01")
        states = await asyncio.wait_for(self.execute(reviewer, checkpoints, budget, jobs=self.jobs[:2]), 2)
        self.assertEqual(reviewer.calls, [("c01", 1)])
        self.assertEqual(budget.ledger["sessions"], 1)
        self.assertFalse(budget.reserved)
        self.assertTrue(all(s["stop_reason"] == "billing_error" for s in states))

    async def test_intent_write_failure_releases_unsent_reservation_and_wakes_waiter(self):
        self.config["runtime"].update(reservation_input_tokens=1000, max_output_tokens=100, max_tool_turns=2)
        checkpoints, budget = self.resources()
        budget.limit = .0015
        original_reserve, original_save = budget.reserve_review, checkpoints.save

        async def reserve_with_waiter(request_id, payload, runtime):
            amount = await original_reserve(request_id, payload, runtime)
            if "b0001" in request_id:
                await asyncio.sleep(.02)  # Let the sibling block on this reservation.
            return amount

        def fail_request_intent(key, value):
            if key == "parallel__case" and any(p.get("in_flight") for p in
                    (value.get("pending") or {}).get("batches", [])):
                raise OSError("synthetic unsent intent failure")
            original_save(key, value)

        reviewer = ConcurrentReviewer()
        with patch.object(budget, "reserve_review", reserve_with_waiter), patch.object(checkpoints, "save", fail_request_intent):
            with self.assertRaisesRegex(OSError, "unsent intent"):
                await asyncio.wait_for(self.execute(reviewer, checkpoints, budget, jobs=self.jobs[:2]), 2)
        self.assertEqual(reviewer.calls, [])
        self.assertEqual(budget.ledger["sessions"], 0)
        self.assertEqual(budget.spent, 0)
        self.assertFalse(budget.reserved)

    async def test_external_cancellation_drains_receipts_for_resume(self):
        reviewer = ConcurrentReviewer(.05)
        checkpoints, budget = self.resources()
        task = asyncio.create_task(self.execute(reviewer, checkpoints, budget, jobs=self.jobs[:2]))
        while reviewer.maximum_active < 2:
            await asyncio.sleep(.001)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(reviewer.active, 0)
        self.assertEqual(budget.ledger["sessions"], 2)
        resumed = ConcurrentReviewer()
        states = await self.execute(resumed, checkpoints, budget, jobs=self.jobs[:2])
        self.assertEqual(resumed.calls, [])
        self.assertTrue(all(s["rounds_completed"] == 1 for s in states))
        self.assertEqual(budget.ledger["sessions"], 2)

    async def test_receipt_write_failure_drains_sibling_and_never_resends_lost_response(self):
        reviewer = ConcurrentReviewer(lambda cid: .005 if cid == "c01" else .04)
        checkpoints, budget = self.resources()
        original_save = checkpoints.save

        def fail_one_receipt(key, value):
            if key == "parallel__case_b0001_review":
                raise OSError("synthetic response journal failure")
            original_save(key, value)

        with patch.object(checkpoints, "save", fail_one_receipt):
            with self.assertRaisesRegex(OSError, "response journal"):
                await self.execute(reviewer, checkpoints, budget, jobs=self.jobs[:2])
        self.assertEqual(reviewer.active, 0)
        self.assertEqual(budget.ledger["sessions"], 2)
        self.assertFalse(budget.reserved)
        resumed = ConcurrentReviewer()
        states = await self.execute(resumed, checkpoints, budget, jobs=self.jobs[:2])
        self.assertEqual(resumed.calls, [])
        self.assertEqual(budget.ledger["sessions"], 2)
        self.assertAlmostEqual(budget.spent, 2 * budget.cost(USAGE))
        self.assertEqual(states[0]["stop_reason"], "service_error")
        self.assertEqual(states[1]["rounds_completed"], 1)

    async def test_failure_receipt_recovery_never_repeats_a_completed_failed_attempt(self):
        reviewer = ConcurrentReviewer(.005, fail="c01")
        checkpoints, budget = self.resources()
        original_save = checkpoints.save

        def crash_after_failure_receipt(key, value):
            if key == "parallel__case" and any(p.get("failure_receipts") for p in
                    (value.get("pending") or {}).get("batches", [])):
                raise OSError("synthetic failed-attempt commit interruption")
            original_save(key, value)

        with patch.object(checkpoints, "save", crash_after_failure_receipt):
            with self.assertRaisesRegex(OSError, "failed-attempt"):
                await self.execute(reviewer, checkpoints, budget, jobs=self.jobs[:1])
        resumed = ConcurrentReviewer()
        states = await self.execute(resumed, checkpoints, budget, jobs=self.jobs[:1])
        self.assertEqual(resumed.calls, [])
        self.assertEqual(budget.ledger["sessions"], 1)
        self.assertEqual(states[0]["stop_reason"], "billing_error")
        self.assertEqual(len(states[0]["history"]), 1)

    async def test_legacy_single_pending_receipt_is_migrated_without_resampling(self):
        jobs = self.jobs[:1]
        state = initialize_candidate("c01", "CC", self.peaks, {})
        key = "parallel__case_b0001_review"
        pending = {"ids": ["c01"], "receipt_key": key, "hashes": {"c01": digest(state["graph"])}, "attempt": 1}
        checkpoints, budget = self.resources()
        checkpoints.save("parallel__case", {"identity": digest(["parallel", jobs, self.peaks]),
                         "states": {"c01": state}, "sequence": 1, "pending": pending})
        review = {"reviews": [{"candidate_id": "c01", "decision": "keep", "proposals": [], "summary": "Stored result"}],
                  "usage": USAGE}
        request_id = key + "_a0_0"
        await budget.reserve(request_id, 1)
        checkpoints.save(key, {"hashes": pending["hashes"], "request_id": request_id, "review": review})
        reviewer = ConcurrentReviewer()
        states = await self.execute(reviewer, checkpoints, budget, jobs=jobs)
        self.assertEqual(reviewer.calls, [])
        self.assertEqual(states[0]["rounds_completed"], 1)
        self.assertEqual(budget.ledger["sessions"], 1)
        self.assertEqual(checkpoints.load("parallel__case")["checkpoint_version"], 2)


if __name__ == "__main__":
    unittest.main()
