"""No-network regression checks for shared paid-stage budgets and cost reports."""
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

PROJECT = Path(__file__).resolve().parents[2]

def script(name, directory='scripts'):
    spec = importlib.util.spec_from_file_location(name, PROJECT / directory / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

validation = script('run_economy_validation', 'scripts/archive/pilot20_20261103_04')
pilot = script('economy_pilot')


def ledger(spent=0, pending=None, **kwargs):
    return {'currency':'CNY', 'spent_cny':spent, 'pending_reservations':pending or {}, **kwargs}


class ValidationBudgetTests(unittest.TestCase):
    def test_known_overruns_reduce_next_stage_ceiling(self):
        prior = {'regression':ledger(5), 'optimized':ledger(12)}
        self.assertEqual(validation.validation_budget('legacy', prior),10)

    def test_current_unknown_commitment_counted_once(self):
        self.assertEqual(validation.validation_budget('regression',{'regression':ledger(.5,{'inflight':.5})}),2)
        with self.assertRaisesRegex(ValueError,'exhausted'):
            validation.validation_budget('regression',{'regression':ledger(1,{'inflight':1})})

    def test_other_pending_or_different_currency_refused(self):
        with self.assertRaisesRegex(ValueError,'in-flight'):
            validation.validation_budget('legacy',{'optimized':ledger(1,{'inflight':.2})})
        with self.assertRaisesRegex(ValueError,'CNY'):
            validation.validation_budget('legacy',{'optimized':{'currency':'USD','spent_usd':2}})

    def test_saved_limit_cannot_be_silently_lowered(self):
        with self.assertRaisesRegex(ValueError,'saved phase limit'):
            validation.validation_budget('legacy', {'regression':ledger(5),'optimized':ledger(12),'legacy':ledger(1,limit_cny=15)})

    def test_nonblocking_os_lock_prevents_simultaneous_stages(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'shared.lock'
            with validation.validation_lock(path):
                with self.assertRaisesRegex(ValueError,'shared budget lock'):
                    with validation.validation_lock(path):
                        self.fail('second stage acquired shared lock')
            with validation.validation_lock(path):
                pass


class CostReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory(); self.root=Path(self.tmp.name)
        (self.root/'logs').mkdir(); (self.root/'results').mkdir()
        self.manifest={'sample_ids':['a','b'],'mode':'agent','backend':'claude_agent_sdk'}
        self.requests=[{'request_id':x,'sample_id':x,'usage':{'known':True,'input_tokens':100,'output_tokens':100}} for x in ('a','b')]
        for case in ('a','b'):
            (self.root/'results'/f'{case}.json').write_text(json.dumps({'sample_id':case,'ranking_complete':True}))

    def tearDown(self):
        self.tmp.cleanup()

    def report(self, **overrides):
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))
        (self.root/'logs/budget.json').write_text(json.dumps(ledger(.1,requests=self.requests,**overrides)))
        return pilot.report_run(self.root)

    def test_unknown_partial_tokens_are_excluded_and_reservation_reported(self):
        self.requests.append({'request_id':'unknown','sample_id':'a','usage':{'known':False,'input_tokens':999999},'cost_cny':.2})
        report=self.report(pending={'pending':.3})
        self.assertEqual(report['usage']['input_tokens'],200)
        self.assertAlmostEqual(report['unknown_usage_reserved_cost'],.5)
        self.assertEqual(report['unknown_usage_sessions'],2)
        self.assertFalse(report['can_project'])
        self.assertNotIn('case_offpeak_cny',report)

    def test_baseline_and_injected_client_do_not_project(self):
        self.manifest.update(mode='baseline',backend='deterministic_baseline'); self.requests=[]
        self.assertFalse(self.report()['can_project'])
        self.manifest.update(mode='agent',backend='injected_client')
        self.requests=[{'request_id':x,'sample_id':x,'usage':{'known':True,'input_tokens':100}} for x in ('a','b')]
        self.assertFalse(self.report()['can_project'])

    def test_every_case_needs_real_usage(self):
        self.requests.pop()
        report=self.report()
        self.assertFalse(report['can_project'])
        self.assertIn('missing_positive_known_usage_for_one_or_more_cases',report['projection_blockers'])

    def test_live_complete_known_report_projects(self):
        report=self.report()
        self.assertTrue(report['can_project'])
        self.assertAlmostEqual(report['projection']['offpeak_cny'],.745)

    def test_unrecorded_backend_keeps_cost_but_cannot_project(self):
        self.manifest.pop('backend')
        report=self.report()
        self.assertFalse(report['can_project'])
        self.assertEqual(report['backend'],'unrecorded')
        self.assertGreater(report['known_usage_peak_cny'],0)


if __name__ == '__main__':
    unittest.main()
