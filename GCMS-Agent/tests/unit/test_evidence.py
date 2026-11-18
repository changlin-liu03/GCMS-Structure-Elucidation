import copy
import hashlib
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from gcms_agent.chemistry.smiles_io import parse_smiles
from gcms_agent.fragmentation.conservation import masses, check_conservation, ELECTRON_MASS
from gcms_agent.fragmentation.enumeration import analyze_spectrum
from gcms_agent.fragmentation.rule_engine import DEFAULT_RULE_PATH, enumerate_paths, load_rule_policy
from gcms_agent.scoring.support import calculate_support
from gcms_agent.scoring.acceptance import accept_edit
from gcms_agent.scoring.ranking import rank_candidates


def peaks(masses_):
    return [{"mz": mz, "intensity": 1., "lower": 1., "upper": 1.} for mz in masses_]


class EvidenceTests(unittest.TestCase):
    def analyze(self, smiles, mz):
        parsed = parse_smiles(smiles)
        self.assertTrue(parsed["valid"], parsed.get("errors"))
        result = analyze_spectrum(parsed["graph"], peaks(mz))
        self.assertTrue(result["complete"], result)
        return result

    def test_ethane_has_named_hydrogen_losses_not_auto_completed_fragments(self):
        result = self.analyze("CC", [15, 28, 29, 30, 31, 45])
        self.assertEqual(result["supported_peaks"], [15, 28, 29, 30])
        h2 = next(e for e in result["evidence"] if e["rule_id"] == "hydrogen_molecule_loss")
        self.assertEqual(h2["ion_formula"], "C2H4")
        self.assertEqual(h2["neutral_losses"][0]["formula"], "H2")
        self.assertEqual(sum(h2["hydrogen_changes"].values()), -2)
        self.assertTrue(result["diagnostics"]["isotopes"])

    def test_acetone_alpha_and_secondary_co(self):
        result = self.analyze("CC(=O)C", [15, 43, 58, 60])
        acyl = [e for e in result["evidence"] if e["rule_id"] == "carbonyl_alpha"]
        self.assertTrue(acyl)
        self.assertEqual(acyl[0]["ion_formula"], "C2H3O")
        self.assertEqual(acyl[0]["neutral_losses"][0]["formula"], "CH3")
        self.assertTrue(any(e["rule_id"] == "acylium_co_loss" and e["steps"] == 2 for e in result["evidence"]))
        self.assertNotIn(60, result["supported_peaks"])

    def test_ethanol_oxonium_and_dehydration(self):
        result = self.analyze("CCO", [28, 31, 46])
        self.assertTrue(any(e["rule_id"] == "heteroatom_alpha" and e["ion_formula"] == "CH3O" for e in result["evidence"]))
        water = next(e for e in result["evidence"] if e["rule_id"] == "alcohol_dehydration")
        self.assertEqual(water["ion_formula"], "C2H4")
        self.assertEqual(water["neutral_losses"][0]["formula"], "H2O")

    def test_mclafferty_requires_gamma_h_and_tracks_transfer(self):
        result = self.analyze("CC(=O)CCCC", [58, 100])
        paths = [e for e in result["evidence"] if e["rule_id"] == "mclafferty"]
        self.assertTrue(paths)
        self.assertEqual(paths[0]["ion_formula"], "C3H6O")
        self.assertEqual(paths[0]["neutral_losses"][0]["formula"], "C3H6")
        self.assertEqual(sorted(paths[0]["hydrogen_changes"].values()), [-1, 1])
        short = self.analyze("CC(=O)C", [58])
        self.assertFalse(any(e["rule_id"] == "mclafferty" for e in short["evidence"]))

    def test_ring_is_not_arbitrarily_cut(self):
        result = self.analyze("C1CC1", [15, 29, 42])
        self.assertNotIn(15, result["supported_peaks"])
        self.assertNotIn(29, result["supported_peaks"])

    def test_kekule_input_still_recognizes_benzylic_cleavage(self):
        result = self.analyze("CCc1ccccc1", [91, 106])
        self.assertTrue(any(e["rule_id"] == "benzylic" and e["peak_mz"] == 91 for e in result["evidence"]))

    def test_invalid_parent_never_receives_evidence(self):
        graph = parse_smiles("CC")["graph"]
        graph["atoms"][0]["hydrogens"] = 8
        result = analyze_spectrum(graph, peaks([15, 30]))
        self.assertFalse(result["complete"])
        self.assertEqual(result["evidence"], [])

    def test_isotope_mass_and_electron_correction(self):
        ion = masses(Counter({("C", 13): 1, ("H", 0): 4}), 1)
        self.assertEqual(ion["nominal_mass"], 17)
        self.assertAlmostEqual(ion["exact_mz"], 13.00335484 + 4 * 1.007825032 - ELECTRON_MASS, places=6)

    def test_false_mass_balance_rejected(self):
        p = Counter({("C", 0): 2, ("H", 0): 6})
        self.assertFalse(check_conservation(p, Counter({("C", 0): 1, ("H", 0): 4}), [Counter({("C", 0): 1, ("H", 0): 3})]))
        self.assertFalse(check_conservation(p, p, [Counter({("H", 0): -1})]))

    def test_h_losses_are_not_applied_to_arbitrary_saturated_carbons(self):
        result = self.analyze("CCCCCC", [84, 85, 86])
        self.assertFalse(any(e["rule_id"] in ("hydrogen_radical_loss", "hydrogen_molecule_loss") for e in result["evidence"]))

    def test_hypervalent_sulfur_is_not_treated_as_amine_donor(self):
        graph = parse_smiles("CCS(=O)(=O)C")["graph"]
        paths = enumerate_paths(graph, load_rule_policy())
        self.assertFalse(any(e["rule_id"] == "heteroatom_alpha" for e in paths))

    def test_path_budget_failure_is_not_complete_zero_support(self):
        graph = parse_smiles("CC")["graph"]
        policy = load_rule_policy()
        policy["max_paths"] = 1
        with self.assertRaisesRegex(ValueError, "enumeration_limit"):
            enumerate_paths(graph, policy)

    def test_default_policy_file_controls_scoring_and_reports_actual_version(self):
        graph = parse_smiles("CC")["graph"]
        spectrum = peaks([30])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rules.json"
            with patch("gcms_agent.fragmentation.rule_engine.DEFAULT_RULE_PATH", path):
                for enabled, expected in ((["molecular_ion"], 1.), ([], 0.)):
                    policy = {"version": f"test-{expected}", "enabled": enabled, "max_steps": 2}
                    path.write_text(json.dumps(policy), encoding="utf-8")
                    evidence = analyze_spectrum(graph, spectrum)
                    self.assertEqual(calculate_support(spectrum, evidence["supported_peaks"])["score"], expected)
                    self.assertEqual(evidence["diagnostics"]["rule_version"], policy["version"])
                    self.assertEqual(evidence["diagnostics"]["enabled_rules"], enabled)
                    self.assertEqual(evidence["diagnostics"]["rule_policy_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
                    self.assertTrue(all(e["rule_version"] == policy["version"] for e in evidence["evidence"]))

    def test_manifest_fingerprints_the_actual_default_policy(self):
        from gcms_agent.config import PROJECT_ROOT, digest
        from gcms_agent.workflow.runner import source_versions
        self.assertEqual(DEFAULT_RULE_PATH, PROJECT_ROOT / "resources" / "ei_rules" / "rules.json")
        self.assertEqual(source_versions()["resources/ei_rules/rules.json"], digest(DEFAULT_RULE_PATH.read_text(encoding="utf-8")))
        self.assertEqual(load_rule_policy()["version"], json.loads(DEFAULT_RULE_PATH.read_text(encoding="utf-8"))["version"])

    def test_enumeration_is_deterministic_and_does_not_mutate(self):
        graph = parse_smiles("CC(=O)CCCC")["graph"]
        original = copy.deepcopy(graph)
        a = enumerate_paths(graph, load_rule_policy())
        b = enumerate_paths(graph, load_rule_policy())
        self.assertEqual(a, b)
        self.assertEqual(graph, original)
        self.assertTrue(all(p["steps"] <= 2 for p in a))


class ScoreTests(unittest.TestCase):
    def test_support_and_bounds(self):
        score = calculate_support(peaks([10, 20]), [10, 10])
        self.assertAlmostEqual(score["score"], .5)
        self.assertEqual(score["score_lower"], .5)
        self.assertEqual(score["score_upper"], .5)
        self.assertAlmostEqual(sum(p["contribution"] for p in score["peak_contributions"]), score["score"])
        self.assertEqual(calculate_support(peaks([10, 20]), [10, 20])["score_lower"], 1)
        self.assertEqual(calculate_support(peaks([10, 20]), [])["score_upper"], 0)

    def test_repair_accepts_valid_zero_support(self):
        empty = calculate_support(peaks([99]), [])
        self.assertTrue(accept_edit({"valid": False}, {"valid": True}, None, empty)["accepted"])
        self.assertFalse(accept_edit({"valid": True}, {"valid": False}, empty, empty)["accepted"])
        self.assertTrue(accept_edit({"valid": False}, {"valid": True}, None, None)["accepted"])

    def test_legal_incomplete_evidence_stays_unranked(self):
        row = {"candidate_id": "a", "final_smiles": "CC", "validation": {"valid": True}, "score": None, "evidence": {"complete": False}}
        result = rank_candidates([row])
        self.assertEqual(result["status"], "evidence_incomplete")
        self.assertEqual(result["unranked_candidates"][0]["final_smiles"], "CC")
        self.assertFalse(result["ranking_complete"])
        self.assertIsNone(result["top1"])
        verdict = accept_edit({"valid": True, "canonical_smiles": "C=C"}, {"valid": True, "canonical_smiles": "CC"}, None, None)
        self.assertFalse(verdict["accepted"])
        self.assertEqual(verdict["reason"], "evidence_incomplete")

    def test_numerical_input_rejections(self):
        bad = peaks([10])[0]
        bad["intensity"] = float("nan")
        with self.assertRaises(ValueError):
            calculate_support([bad], [10])
        with self.assertRaises(ValueError):
            calculate_support(peaks([10, 10]), [10])
        with self.assertRaises(ValueError):
            calculate_support([], [])
        score = calculate_support(peaks([10]), [10])
        verdict = accept_edit({"valid": True, "canonical_smiles": "CC"},
                              {"valid": True, "canonical_smiles": "C=C"}, score,
                              {**score, "score": float("nan")})
        self.assertFalse(verdict["accepted"])
        self.assertEqual(verdict["reason"], "invalid_score_bounds")

    def test_positive_weak_peak_is_not_quantized_or_filtered(self):
        p = {"mz": 10, "intensity": 1e-9}
        self.assertEqual(calculate_support([p], [10])["score_lower"], 1)
        self.assertEqual(calculate_support([p], [])["score_upper"], 0)
        with self.assertRaises(ValueError):
            calculate_support([{**p, "intensity": -1}], [10])

    def test_exploration_accepts_improvement_but_rejects_same_identity(self):
        old = calculate_support(peaks([10, 20]), [10])
        new = calculate_support(peaks([10, 20]), [10, 20])
        self.assertTrue(accept_edit({"valid": True, "canonical_smiles": "CC"}, {"valid": True, "canonical_smiles": "C=C"}, old, new)["accepted"])
        self.assertFalse(accept_edit({"valid": True, "canonical_smiles": "CC"}, {"valid": True, "canonical_smiles": "CC"}, old, new)["accepted"])

    def test_duplicate_sources_do_not_inflate_ranking(self):
        score = calculate_support(peaks([10, 20]), [10])
        rows = [{"candidate_id": "a", "final_smiles": "CC", "validation": {"valid": True}, "score": score},
                {"candidate_id": "b", "final_smiles": "CC", "validation": {"valid": True}, "score": score},
                {"candidate_id": "c", "final_smiles": "C=C", "validation": {"valid": True}, "score": score}]
        result = rank_candidates(rows)
        self.assertEqual(len(result["ranked_candidates"]), 3)
        self.assertEqual(len(result["unique_ranking"]), 2)
        self.assertTrue(all(r["tied"] for r in result["unique_ranking"]))
        self.assertEqual(rank_candidates(list(reversed(rows)))["top1"]["final_smiles"], result["top1"]["final_smiles"])
        self.assertEqual(result["top1"]["score"]["score"], .5)

    def test_zero_support_and_failures_have_no_top1(self):
        self.assertIsNone(rank_candidates([{"candidate_id": "a", "final_smiles": None}])["top1"])
        result = rank_candidates([{"candidate_id": "a", "final_smiles": "CC", "validation": {"valid": True}, "score": calculate_support(peaks([999]), [])}])
        self.assertEqual(result["status"], "no_supported_peaks")
        self.assertIsNone(result["top1"])


if __name__ == "__main__":
    unittest.main()
