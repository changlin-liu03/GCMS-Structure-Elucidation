"""Offline execution of the real workflow with a deterministic model double.

Fixtures are synthetic and contain no reference structures or test-set labels.
"""
import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gcms_agent.config import load_config
from gcms_agent.data.loader import validate_input
from gcms_agent.data.intensity import raw_peaks
from gcms_agent.workflow.budget import Budget, BudgetExhausted
from gcms_agent.workflow.checkpoint import Checkpoints
from gcms_agent.workflow.candidate import process_candidate
from gcms_agent.workflow.runner import run_batch


class MockReviewer:
    def __init__(self, proposal_factory=None, delay=0):
        self.calls = 0
        self.proposal_factory = proposal_factory
        self.delay = delay

    async def review(self, payload):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        proposals = self.proposal_factory(payload) if self.proposal_factory else []
        return {"proposals": proposals, "summary": "Synthetic reviewer result", "format_errors": 0,
                "actual_models": ["mock-only"],
                "usage": {"known": True, "input_tokens": 10, "output_tokens": 10, "cache_read_input_tokens": 0}}


def repair_five_membered_ring(payload):
    if payload["round"] != 1:
        return []
    graph = payload["graph"]
    operations = [{"op": "set_bond", "a": b["a"], "b": b["b"], "order": 1} for b in graph["bonds"]]
    operations += [{"op": "set_atom", "id": a["id"], "hydrogens": 2, "radicals": 0} for a in graph["atoms"]]
    return [{"base_version": payload["graph_version"], "operations": operations,
             "reason": "Synthetic chemical-validity repair with deliberately no spectral support", "evidence_peaks": []}]


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gcms_workflow_test_")
        self.directory = Path(self.temp.name)
        self.config = copy.deepcopy(load_config())
        self.config["runtime"].update(review_strategy="legacy", budget_currency="USD", offpeak_only=False)
        self.checkpoints = Checkpoints(self.directory / "checkpoints")
        self.budget = Budget(10, self.config["model"]["prices"], self.directory / "budget.json")
        self.row = {"sample_id": "synthetic", "input_ids": [499], "intensity": [29], "generated": ["CC"] * 10}
        self.peaks = raw_peaks([499], [29])

    def tearDown(self):
        self.temp.cleanup()

    async def process(self, smiles, client=None, config=None):
        return await process_candidate(self.row, "c01", smiles, self.peaks, config or self.config,
                                       client or MockReviewer(), self.budget, self.checkpoints)

    async def test_two_complete_no_gain_rounds_stop_and_resume_no_new_calls(self):
        reviewer = MockReviewer()
        result = await self.process("CC", reviewer)
        self.assertEqual(reviewer.calls, 2)
        self.assertEqual(result["stop_reason"], "stagnation")
        self.assertEqual(result["stagnation_count"], 2)
        self.assertEqual(result["result_status"], "kept")
        self.assertEqual(result["final_smiles"], "CC")
        again = await self.process("CC", reviewer)
        self.assertEqual(reviewer.calls, 2)
        self.assertEqual(result, again)

    async def test_ten_operation_repair_is_saved_even_with_zero_spectral_score(self):
        reviewer = MockReviewer(repair_five_membered_ring)
        result = await self.process("c1cccc1", reviewer)
        self.assertTrue(result["validation"]["valid"], result["validation"])
        self.assertEqual(result["final_smiles"], "C1CCCC1")
        self.assertEqual(result["result_status"], "corrected")
        self.assertEqual(result["score"]["score"], 0)
        self.assertEqual(result["spectral_status"], "spectrally_unsupported")
        self.assertEqual(result["accepted_edits"], 1)
        self.assertEqual(result["rounds_completed"], 3)
        self.assertEqual(result["stagnation_count"], 2)
        first = next(h for h in result["history"] if "outcomes" in h)["outcomes"][0]
        self.assertEqual(len(first["proposal"]["operations"]), 10)
        self.assertTrue(first["verdict"]["accepted"])

    async def test_unrepaired_invalid_graph_never_exports_raw_smiles(self):
        result = await self.process("c1cccc1")
        self.assertFalse(result["validation"]["valid"])
        self.assertIsNone(result["final_smiles"])
        self.assertEqual(result["result_status"], "invalid_unresolved")

    async def test_timeout_is_not_chemical_stagnation(self):
        config = copy.deepcopy(self.config)
        config["runtime"]["candidate_timeout_seconds"] = .01
        result = await self.process("CC", MockReviewer(delay=.2), config)
        self.assertEqual(result["stop_reason"], "timeout")
        self.assertEqual(result["stagnation_count"], 0)
        self.assertEqual(result["rounds_completed"], 0)
        self.assertEqual(result["final_smiles"], "CC")

    async def test_ten_lineages_deduplicate_and_completed_batch_resumes(self):
        row = {**self.row, "generated": ["CC"] * 5 + ["C=C"] * 5}
        source = self.directory / "inputs.json"
        source.write_text(json.dumps([row]), encoding="utf-8")
        destination = self.directory / "run"
        reviewer = MockReviewer()
        with patch("gcms_agent.workflow.runner.source_versions", return_value={"synthetic_version": "1"}):
            summary = await run_batch(source, destination, self.config, client=reviewer)
            self.assertEqual(reviewer.calls, 4)
            self.assertEqual(summary["complete_samples"], 1)
            result = json.loads((destination / "results" / "synthetic.json").read_text(encoding="utf-8"))
            self.assertEqual(len(result["ranked_candidates"]), 10)
            self.assertEqual(len(result["unique_ranking"]), 2)
            self.assertTrue(all(r["final_smiles"] for r in result["ranked_candidates"]))
            self.assertIsNone(result["top1"])
            await run_batch(source, destination, self.config, client=reviewer, resume=True)
            self.assertEqual(reviewer.calls, 4)

    async def test_all_invalid_slots_are_retained_in_batch(self):
        source = self.directory / "inputs.json"
        source.write_text(json.dumps([{**self.row, "generated": ["C1CC"] * 10}]), encoding="utf-8")
        reviewer = MockReviewer()
        destination = self.directory / "invalid_run"
        await run_batch(source, destination, self.config, client=reviewer)
        result = json.loads((destination / "results" / "synthetic.json").read_text(encoding="utf-8"))
        self.assertEqual(reviewer.calls, 0)
        self.assertEqual(len(result["ranked_candidates"]), 10)
        self.assertTrue(all(r["final_smiles"] is None for r in result["ranked_candidates"]))
        self.assertIsNone(result["top1"])

    async def test_budget_prevents_request_and_marks_batch_incomplete(self):
        source = self.directory / "inputs.json"
        source.write_text(json.dumps([self.row]), encoding="utf-8")
        config = copy.deepcopy(self.config)
        config["runtime"]["budget_usd"] = .0000000001
        reviewer = MockReviewer()
        destination = self.directory / "budget_run"
        summary = await run_batch(source, destination, config, client=reviewer)
        self.assertEqual(reviewer.calls, 0)
        self.assertEqual(summary["spent_usd"], 0)
        result = json.loads((destination / "results" / "synthetic.json").read_text(encoding="utf-8"))
        self.assertFalse(result["ranking_complete"])
        self.assertEqual(result["ranking_status"], "provisional/incomplete")

    def test_labels_are_rejected_by_inference_loader(self):
        with self.assertRaisesRegex(ValueError, "forbidden"):
            validate_input({**self.row, "true_smiles": "NEVER_PASS_TO_AGENT"})
        with self.assertRaises(ValueError):
            validate_input({**self.row, "conditions": {"true_smiles": "NEVER_PASS_TO_AGENT"}})

    async def test_pending_reservations_share_one_limit(self):
        budget = Budget(1.5, self.config["model"]["prices"], self.directory / "shared.json")
        await budget.reserve("a", 1)
        with self.assertRaises(BudgetExhausted):
            await budget.reserve("b", 1)
        await budget.settle("a", {"known": False})
        self.assertEqual(budget.ledger["spent_usd"], 1)
        with self.assertRaises(BudgetExhausted):
            await budget.reserve("b", 1)


if __name__ == "__main__":
    unittest.main()
