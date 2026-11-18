import tempfile
import unittest
from pathlib import Path

from gcms_agent.config import write_json
from gcms_agent.evaluation.metrics import evaluate_results
from gcms_agent.evaluation.runner import evaluate


class EvaluationAbstentionTests(unittest.TestCase):
    def test_null_top1_is_not_a_hit_from_lexical_display_order(self):
        result = {"sample_id": "a", "unique_ranking": [{"final_smiles": "CC"}],
                  "top1": None, "ranked_candidates": []}
        score = evaluate_results([result], [{"sample_id": "a", "true_smiles": "CC"}])
        self.assertEqual(score["acc_at_1"], 0)
        self.assertEqual(score["acc_at_10"], 1)

    def test_incomplete_and_unprocessed_stay_in_strict_and_baseline_denominators(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(root / "manifest.json", {"sample_ids": ["a", "b"]})
            top = {"final_smiles": "CC"}
            write_json(root / "results/a.json", {"sample_id": "a", "ranking_complete": False,
                "top1": top, "unique_ranking": [top], "ranked_candidates": []})
            write_json(root / "labels.json", [{"sample_id": sid, "true_smiles": "CC"} for sid in ("a", "b")])
            write_json(root / "input.json", [{"sample_id": sid, "input_ids": [30], "intensity": [29],
                "generated": ["CC"] * 10} for sid in ("a", "b")])
            report = evaluate(root, root / "labels.json", root / "input.json")
            self.assertEqual(report["requested_set"]["acc_at_1"], .5)
            self.assertEqual(report["strict_end_to_end_requested_set"]["acc_at_1"], 0)
            self.assertEqual(report["strict_end_to_end_requested_set"]["samples"], 2)
            self.assertEqual(report["original_diffusion_order"]["samples"], 2)
            self.assertEqual(report["original_diffusion_order"]["acc_at_1"], 1)
