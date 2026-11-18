"""Readable Top1 reports must retain the mechanism bookkeeping from JSON."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from gcms_agent.reporting.export import export_sample


class Top1ReportTests(unittest.TestCase):
    def test_rearrangement_and_implicit_hydrogen_losses_remain_visible(self):
        score = {"score": .5, "confidence_score": 50, "score_lower": .45, "score_upper": .55,
                 "unassigned_peaks": [15]}
        events = [
            {"verified": True, "peak_mz": 58, "rule_id": "mclafferty", "ion_formula": "C3H6O",
             "charge": 1, "radicals": 1, "exact_mz": 58.0413,
             "atom_ids": ["Mol_C_1", "Mol_C_2", "Mol_C_3", "Mol_O_1"],
             "broken_bonds": [["Mol_C_3", "Mol_C_4"]],
             "hydrogen_changes": {"Mol_O_1": 1, "Mol_C_5": -1},
             "bond_changes": [{"a": "Mol_C_2", "b": "Mol_O_1", "from": 2, "to": 1}],
             "neutral_losses": [{"formula": "C2H4", "atom_ids": ["Mol_C_4", "Mol_C_5"], "charge": 0, "radicals": 0}],
             "verification_scope": "template_and_stoichiometry_only"},
            {"verified": True, "peak_mz": 29, "rule_id": "hydrogen_radical_loss", "ion_formula": "C2H5",
             "charge": 1, "radicals": 0, "exact_mz": 29.0386, "atom_ids": ["Mol_C_1", "Mol_C_2"],
             "broken_bonds": [], "hydrogen_changes": {"Mol_C_1": -1}, "bond_changes": [],
             "neutral_losses": [{"formula": "H", "atom_ids": [], "charge": 0, "radicals": 1}]},
        ]
        # Synthetic report fixture exercises serialization, not molecular inference.
        candidate = {"candidate_id": "c01", "original_smiles": "CCC", "final_smiles": "CCC", "rank": 1,
                     "result_status": "kept", "stop_reason": "max_rounds", "score": score,
                     "history": [], "validation": {"molecode": "graph TB"}, "evidence": {"evidence": events}}
        result = {"sample_id": "synthetic-report", "ranking_status": "complete", "ranked_candidates": [candidate],
                  "top1": {"final_smiles": "CCC", "score": score}, "peaks": []}
        with TemporaryDirectory() as directory, patch("gcms_agent.reporting.figures.render_top1"):
            export_sample(directory, result)
            report = (Path(directory) / "reports/synthetic-report.md").read_text(encoding="utf-8")
        for expected in ("电荷|自由基电子数", "Mol_O_1: +1", "Mol_C_5: -1", "C2H4（原子",
                         "Mol_C_2-Mol_O_1: 2 → 1", "Mol_C_1: -1", "H（原子 []；电荷 0；自由基电子数 1）",
                         "template_and_stoichiometry_only", "不表示已实验证实机制"):
            self.assertIn(expected, report)


if __name__ == "__main__":
    unittest.main()
