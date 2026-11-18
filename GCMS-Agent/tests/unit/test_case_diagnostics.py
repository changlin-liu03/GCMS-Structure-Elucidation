"""Transport exemptions, structure summaries and weak-evidence presentation."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from gcms_agent.harness.transport import unused_connection_failure
from gcms_agent.harness.client import classify_failure
from gcms_agent.harness.prompts import batch_inference_payload
from gcms_agent.workflow.exploration import record_exploration, exploration_summary
from gcms_agent.workflow.candidate import assess
from gcms_agent.chemistry.smiles_io import parse_smiles
from gcms_agent.scoring.ranking import rank_candidates
from gcms_agent.reporting.export import export_sample


class CaseDiagnosticsTests(unittest.TestCase):
    def test_transport_exemption_needs_positive_evidence_of_no_work(self):
        usage = {"known": True, "input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 0}
        receipt = {"error_category": "service_error", "detail": "ECONNREFUSED",
                   "audit": {"trials": [], "tool_metrics": [], "submission_attempt_count": 0}}
        self.assertTrue(unused_connection_failure(receipt, usage))
        for altered in ({"known": False}, {**usage, "output_tokens": 1},
                        {**usage, "cache_read_input_tokens": 1},
                        {k:v for k,v in usage.items() if k != "input_tokens"}):
            self.assertFalse(unused_connection_failure(receipt, altered))
        for field, value in (("trials", [{}]), ("tool_metrics", [{}]), ("submission_attempt_count", 1)):
            changed = deepcopy(receipt)
            changed['audit'][field] = value
            self.assertFalse(unused_connection_failure(changed, usage))
        self.assertFalse(unused_connection_failure({**receipt, "audit": {}}, usage))
        self.assertFalse(unused_connection_failure({**receipt, "detail": "request timeout"}, usage))
        self.assertEqual(classify_failure(None, "ECONNREFUSED"), ("service_error", True))
        self.assertEqual(classify_failure(401, "ECONNREFUSED"), ("authentication_error", False))
        self.assertEqual(classify_failure(None, "insufficient balance ECONNREFUSED"), ("billing_error", False))

    def test_shared_summary_deduplicates_structures_without_exporting_atom_ids(self):
        peaks = [{"mz": 42, "intensity": 1}]
        validation, _, score = assess(parse_smiles('C1CC1')['graph'], peaks)
        archive = {}
        for cid, tid in [('c01', 't1'), ('c02', 't2'), ('c02', 't2')]:
            record_exploration(archive, cid, validation, score, trial_id=tid)
        summary = exploration_summary(archive)
        item = summary['structures'][0]
        self.assertEqual(summary['total_structures'], 1)
        self.assertEqual(item['trial_count'], 2)
        self.assertEqual(item['candidate_ids'], ['c01', 'c02'])
        self.assertEqual(item['ring_sizes'], [3])
        self.assertNotIn('Mol_', json.dumps(summary))
        self.assertNotIn('trial_ids', json.dumps(summary))
        restored = json.loads(json.dumps(archive))
        self.assertEqual(exploration_summary(restored), summary)
        summary['structures'][0]['candidate_ids'].append('mutated')
        self.assertNotIn('mutated', exploration_summary(archive)['structures'][0]['candidate_ids'])

    def test_shared_context_still_rejects_labels(self):
        with self.assertRaises(ValueError):
            batch_inference_payload({'sample_id': 'x', 'candidates': [{'candidate_id': 'c01'}],
                                     'case_exploration': {'true_smiles': 'CC'}})

    def test_report_distinguishes_completed_review_from_molecular_ion_only_support(self):
        peaks = [{'mz': 30, 'intensity': 10}, {'mz': 99, 'intensity': 80}, {'mz': 97, 'intensity': 10}]
        validation, evidence, score = assess(parse_smiles('CC')['graph'], peaks)
        row = {'candidate_id': 'c01', 'original_smiles': 'CC', 'final_smiles': 'CC',
               'validation': validation, 'evidence': evidence, 'score': score,
               'result_status': 'kept', 'stop_reason': 'review_complete', 'history': []}
        ranked = rank_candidates([row])
        self.assertEqual(ranked['top1']['final_smiles'], 'CC')
        self.assertAlmostEqual(ranked['top1']['score']['score'], .1)
        profile = ranked['top1']['evidence_profile']
        self.assertEqual(profile['evidence_status'], 'molecular_ion_only')
        self.assertEqual(profile['strongest_unassigned_peaks'], [{'mz': 99, 'weight': .8}, {'mz': 97, 'weight': .1}])
        with TemporaryDirectory() as directory, patch('gcms_agent.reporting.figures.render_top1'):
            export_sample(directory, {**ranked, 'sample_id': 'weak', 'ranking_status': 'complete', 'peaks': peaks})
            text = (Path(directory) / 'reports/weak.md').read_text(encoding='utf-8')
        self.assertIn('缺少异构体区分证据', text)
        self.assertIn('|99|80|80.00%|', text)
        self.assertIn('不表示结构已确认', text)


if __name__ == '__main__':
    unittest.main()
