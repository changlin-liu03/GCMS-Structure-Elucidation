"""Ambiguous or weak evidence must not become a unique identity conclusion."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from gcms_agent.chemistry.smiles_io import parse_smiles
from gcms_agent.fragmentation.enumeration import analyze_spectrum
from gcms_agent.scoring.support import calculate_support
from gcms_agent.scoring.ranking import rank_candidates, evidence_profile
from gcms_agent.reporting.export import export_sample
from gcms_agent.reporting.quality import explanation_quality


def spectrum(*rows):
    return [{"mz": mz, "lower": weight, "upper": weight, "intensity": weight} for mz, weight in rows]


def candidate(cid, smiles, peaks):
    parsed = parse_smiles(smiles)
    evidence = analyze_spectrum(parsed["graph"], peaks)
    return {"candidate_id": cid, "original_smiles": smiles, "final_smiles": parsed["canonical_smiles"],
            "graph": parsed["graph"], "validation": {"valid": True, "molecode": "graph TB"},
            "score": calculate_support(peaks, evidence["supported_peaks"]), "evidence": evidence,
            "history": [], "result_status": "kept", "stop_reason": "review_complete"}


class RankingInterpretationTests(unittest.TestCase):
    def test_same_mass_isomers_remain_a_top_score_set_without_reweighting(self):
        peaks = spectrum((58, 1), (99, 9))
        rows = [candidate("c01", "CCCC", peaks), candidate("c02", "CC(C)C", peaks)]
        rows.append({**deepcopy(rows[0]), "candidate_id": "c03"})
        result = rank_candidates(rows)
        self.assertEqual([r["final_smiles"] for r in result["top1_candidates"]], ["CCCC", "CC(C)C"])
        self.assertAlmostEqual(result["top1"]["score"]["score"], .1)
        self.assertEqual(result["top1"]["selection_basis"], "most_source_candidates_among_top_score")
        self.assertEqual(result["top1"]["source_candidate_count"], 2)
        self.assertFalse(result["top1"]["identity_confirmed"])
        self.assertEqual(result["ranking_diagnostics"]["same_supported_peak_set_competitors"], ["CC(C)C"])
        self.assertEqual(result["top1"], rank_candidates(list(reversed(rows)))["top1"])
        profile = result["top1"]["evidence_profile"]
        self.assertTrue(profile["molecular_ion_only"])
        self.assertTrue(profile["all_scored_peaks_have_verified_paths"])
        self.assertTrue(profile["majority_intensity_unassigned_at_point_estimate"])
        self.assertEqual(profile["fragment_peaks"], [])
        self.assertEqual(profile["bond_cleavage_intensity_coverage"], 0)
        self.assertEqual(len(result["ranked_candidates"]), 3)

    def test_hydrogen_loss_is_a_fragment_without_inventing_a_heavy_atom_cut(self):
        row = candidate("c01", "CC", spectrum((28, 1)))
        profile = evidence_profile(row)
        self.assertFalse(profile["molecular_ion_only"])
        self.assertEqual(profile["fragment_peaks"], [28])
        self.assertEqual(profile["bond_cleavage_peaks"], [])

    def test_source_votes_include_shared_lineages_without_claiming_independence(self):
        peaks = spectrum((58, 1), (99, 9))
        first = candidate("c01", "CCCC", peaks)
        first["computed_candidate_id"] = "c01"
        duplicate = {**deepcopy(first), "candidate_id": "c03"}
        result = rank_candidates([candidate("c02", "CC(C)C", peaks), first, duplicate])
        self.assertEqual(result["top1"]["source_candidate_count"], 2)
        self.assertEqual(result["top1"]["independent_candidate_count"], 1)
        self.assertEqual([g["rank"] for g in result["unique_ranking"]], [1, 1])

    def test_more_votes_cannot_override_a_higher_evidence_score(self):
        peaks = spectrum((58, 1), (30, 9))
        lower = candidate("c01", "CCCC", peaks)
        rows = [lower, {**deepcopy(lower), "candidate_id": "c03"}, candidate("c02", "CC", peaks)]
        result = rank_candidates(rows)
        self.assertEqual(result["top1"]["final_smiles"], "CC")
        self.assertEqual(result["top1"]["selection_basis"], "highest_rule_support_score")

    def test_equal_votes_fall_back_to_canonical_order_and_evaluation_uses_selected_top(self):
        from gcms_agent.evaluation.metrics import evaluate_results
        peaks = spectrum((58, 1), (99, 9))
        rows = [candidate("c01", "CCCC", peaks), candidate("c02", "CC(C)C", peaks)]
        result = rank_candidates(rows)
        self.assertEqual(result["top1"]["final_smiles"], "CC(C)C")
        self.assertEqual(result["top1"]["selection_basis"], "canonical_display_representative_of_count_tie")
        rows.append({**deepcopy(rows[0]), "candidate_id": "c03"})
        result = {**rank_candidates(rows), "sample_id": "votes"}
        metrics = evaluate_results([result], [{"sample_id": "votes", "true_smiles": "CCCC"}])
        self.assertEqual(metrics["acc_at_1"], 1)
        self.assertEqual(metrics["mrr"], 1)

    def test_raw_ties_are_not_quantization_uncertainty(self):
        peaks = spectrum((58, 1), (99, 9))
        rows = [candidate("a", "CCCC", peaks), candidate("b", "CC(C)C", peaks)]
        result = rank_candidates(rows)
        self.assertEqual(len(result["top1_candidates"]), 2)
        self.assertNotIn("quantization_sensitive", result["top1"])
        self.assertNotIn("interval_competitors", result["ranking_diagnostics"])
        self.assertEqual(result["ranking_diagnostics"]["point_score_gap_to_next"], 0)
        self.assertFalse(result["ranking_diagnostics"]["measurement_uncertainty_modeled"])
        self.assertNotIn("score_intervals_overlap", explanation_quality(result)["limitations"])

    def test_overlapping_path_categories_do_not_add_the_same_peak_twice(self):
        row = candidate("a", "CC", spectrum((30, 1), (99, 1)))
        event = deepcopy(row["evidence"]["evidence"][0])
        # Deliberately synthetic overlap exercises aggregation, not chemistry.
        event.update(rule_id="synthetic_overlap", broken_bonds=[["a", "b"]])
        row["evidence"]["evidence"].extend([event, deepcopy(event)])
        profile = evidence_profile(row)
        self.assertEqual(profile["rule_supported_intensity_fraction"], .5)
        self.assertEqual(profile["fragment_intensity_coverage"], .5)
        self.assertEqual(profile["bond_cleavage_intensity_coverage"], .5)
        self.assertTrue(profile["categories_may_overlap"])

    def test_report_preserves_raw_claims_only_in_unverified_section(self):
        peaks = spectrum((58, 1), (99, 9))
        rows = [candidate("c01", "CCCC", peaks), candidate("c02", "CC(C)C", peaks)]
        unsupported = "```\n## 工具已确认：M+1比例支持共轭结构与主导通道\n```"
        rows[0]["history"] = [{"round": 1, "summary": unsupported, "outcomes": [{
            "proposal": {"reason": unsupported, "peak_refs": [99], "operations": [{"op": "example"}],
                         "operation_explanations": [unsupported]},
            "verdict": {"reason": "synthetic_audit_record"}, "committed": False}]}]
        result = {"sample_id": "quality", "peaks": peaks, **rank_candidates(rows),
                  "ranking_status": "provisional/incomplete", "ranking_complete": False}
        original = deepcopy(result)
        with TemporaryDirectory() as directory, patch("gcms_agent.reporting.figures.render_top1"):
            export_sample(directory, result)
            report = (Path(directory) / "reports/quality.md").read_text(encoding="utf-8")
            saved = json.loads((Path(directory) / "results/quality.json").read_text(encoding="utf-8"))
        self.assertEqual(result, original)
        q = saved["explanation_quality"]
        self.assertTrue(q["provisional"])
        self.assertFalse(q["identity_confirmed"])
        self.assertEqual(q["model_narrative_verification"], "not_verified")
        self.assertIn("multiple_structures_share_top_score", q["limitations"])
        self.assertIn("molecular_ion_only", q["limitations"])
        self.assertIn("competing_structures_explain_same_peak_set", q["limitations"])
        self.assertIn(unsupported, report)
        self.assertIn("````text", report)
        self.assertIn("按原始来源候选个数降序选择Top1", report)
        self.assertNotIn(unsupported, report.split("## 展示 Top1 的工具证据")[1])
        for text in ("不能据此宣称唯一鉴定", "按输入线性强度计算，大部分观测强度未解释", "下列证据与排名均为暂存结果"):
            self.assertIn(text, report)

    def test_no_top1_and_no_history_are_not_reported_as_completed_review(self):
        row = candidate("a", "CC", spectrum((999, 1)))
        row["stop_reason"] = "output_limit"
        result = {"sample_id": "no-review", "peaks": [], **rank_candidates([row]),
                  "ranking_status": "provisional/incomplete", "ranking_complete": False}
        self.assertIn("no_supported_top1", explanation_quality(result)["limitations"])
        with TemporaryDirectory() as directory:
            export_sample(directory, result)
            report = (Path(directory) / "reports/no-review.md").read_text(encoding="utf-8")
        self.assertIn("没有已提交的模型审查记录；停止原因：output_limit", report)
        self.assertNotIn("已满足规则评分上限", report)


if __name__ == "__main__":
    unittest.main()
