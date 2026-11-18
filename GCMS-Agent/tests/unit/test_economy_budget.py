from datetime import datetime, timedelta
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from gcms_agent.config import load_config
from gcms_agent.workflow.budget import Budget, BudgetExhausted, CHINA, DEFAULT_CNY, tariff_at, interval_tariff, token_cost

PROJECT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("economy_pilot", PROJECT / "scripts/economy_pilot.py")
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


class TariffTests(unittest.TestCase):
    def test_weekday_boundaries_and_weekend(self):
        for hour, minute, expected in [(8,59,"offpeak"),(9,0,"peak"),(11,59,"peak"),(12,0,"offpeak"),
                                        (13,59,"offpeak"),(14,0,"peak"),(17,59,"peak"),(18,0,"offpeak")]:
            self.assertEqual(tariff_at(datetime(2026,11,3,hour,minute,tzinfo=CHINA)), expected)
        self.assertEqual(tariff_at(datetime(2026,11,7,10,tzinfo=CHINA)), "offpeak")
        with self.assertRaises(ValueError):
            tariff_at(datetime(2026,11,3,10))

    def test_interval_crossing_peak_counts_whole_session_conservatively(self):
        start = datetime(2026,11,3,8,59,tzinfo=CHINA)
        self.assertEqual(interval_tariff(start, start+timedelta(minutes=3)), "peak")
        self.assertEqual(interval_tariff(start, start+timedelta(hours=5)), "peak")
        self.assertEqual(interval_tariff(datetime(2026,11,6,18,tzinfo=CHINA), datetime(2026,11,9,8,tzinfo=CHINA)), "offpeak")

    def test_known_example_prices(self):
        usage = {"input_tokens":229997,"cache_read_input_tokens":721408,"output_tokens":93923}
        self.assertAlmostEqual(token_cost(usage, DEFAULT_CNY["peak"]),1.24023432)
        self.assertAlmostEqual(token_cost(usage, DEFAULT_CNY["offpeak"]),.62011716)


class BudgetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = TemporaryDirectory()
        self.path = Path(self.tmp.name) / "budget.json"
        self.config = load_config()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_cny_usage_is_counted_once_and_usd_is_not_mixed(self):
        budget = Budget.from_config(self.config, self.path)
        await budget.reserve("r", 1)
        usage = {"known":True,"input_tokens":100000,"cache_read_input_tokens":100000,"output_tokens":100000}
        await budget.settle("r",usage)
        await budget.settle("r",usage)
        self.assertAlmostEqual(budget.spent,1.004)
        self.assertNotIn("spent_usd",budget.ledger)
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved["sessions"],1)
        self.assertEqual(saved["usage"]["output_tokens"],100000)
        with self.assertRaisesRegex(ValueError,"currency"):
            Budget(5,self.config["model"]["prices"],self.path,saved)

    async def test_unknown_recovery_is_conservative_and_receipt_reconciles(self):
        budget = Budget.from_config(self.config,self.path)
        await budget.reserve("lost",.9)
        previous = json.loads(self.path.read_text())
        recovered = Budget.from_config(self.config,self.path,previous)
        self.assertAlmostEqual(recovered.spent,.9)
        self.assertFalse(recovered.reserved)
        await recovered.settle("lost",{"known":True,"input_tokens":1000,"output_tokens":1000})
        self.assertAlmostEqual(recovered.spent,.01)
        self.assertFalse(recovered.ledger["requests"][0]["estimated"])

    async def test_actual_reservation_overrun_blocks_new_sessions(self):
        self.config["runtime"]["budget_cny"] = 30
        budget = Budget.from_config(self.config,self.path)
        await budget.reserve_review("r",{"sample_id":"s","candidates":[]},self.config["runtime"])
        await budget.settle("r",{"known":True,"input_tokens":1000000,"output_tokens":1000000})
        with self.assertRaises(BudgetExhausted) as raised:
            await budget.reserve("later",.01)
        self.assertEqual(raised.exception.category,"reservation_exceeded")

    async def test_offpeak_guard_does_not_reserve(self):
        self.config["runtime"]["offpeak_only"] = True
        budget = Budget.from_config(self.config,self.path)
        with patch("gcms_agent.workflow.budget.tariff_at",return_value="peak"):
            with self.assertRaises(BudgetExhausted) as raised:
                await budget.reserve_review("r",{},self.config["runtime"])
        self.assertEqual(raised.exception.category,"offpeak_wait")
        self.assertEqual(budget.spent,0)
        self.assertEqual(budget.reserved,{})

    async def test_resume_requires_explicit_sufficient_reservation_increase(self):
        budget = Budget.from_config(self.config,self.path)
        await budget.reserve_review("overrun",{},self.config["runtime"])
        await budget.settle("overrun",{"known":True,"input_tokens":1000000,"output_tokens":1000000})
        saved = json.loads(self.path.read_text())
        unchanged = Budget.from_config(self.config,self.path,saved)
        self.assertTrue(unchanged.ledger["reservation_exceeded"])
        self.config["runtime"]["reservation_input_tokens"] = 6000000
        increased = Budget.from_config(self.config,self.path,saved)
        self.assertFalse(increased.ledger["reservation_exceeded"])
        self.assertEqual(increased.ledger["reservation_revisions"][0]["input_tokens"],6000000)
        self.assertEqual(increased.spent,budget.spent)

    async def test_unknown_charge_and_limit(self):
        budget = Budget(.3,DEFAULT_CNY["peak"],self.path,currency="CNY")
        await budget.reserve("r",.2)
        with self.assertRaises(BudgetExhausted):
            await budget.reserve("s",.2)
        await budget.settle("r",{"known":False})
        self.assertAlmostEqual(budget.spent,.2)
        self.assertEqual(budget.ledger["unknown_usage_sessions"],1)


class PilotTests(unittest.TestCase):
    def test_labels_are_rejected_and_selection_is_reproducible(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            rows=[{"sample_id":f"case_{i}","generated":["CCO"]*10,"input_ids":[31,46],"intensity":[29,20]} for i in range(6)]
            src=root/"input.json"; src.write_text(json.dumps(rows))
            a=pilot.prepare_pilot(src,root/"a.json",3)
            b=pilot.prepare_pilot(src,root/"b.json",3)
            self.assertEqual(a["sample_ids"],b["sample_ids"])
            self.assertEqual(sum(s["selected"] for s in a["strata"].values()),3)
            rows[0]["true_smiles"]="CCO"; src.write_text(json.dumps(rows))
            with self.assertRaisesRegex(ValueError,"forbidden"):
                pilot.prepare_pilot(src,root/"bad.json",3)

    def test_report_never_projects_partial_or_unknown_usage(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp); (root/"logs").mkdir(); (root/"results").mkdir()
            (root/"manifest.json").write_text(json.dumps({"sample_ids":["a","b"]}))
            (root/"results/a.json").write_text(json.dumps({"sample_id":"a","ranking_complete":True}))
            (root/"logs/budget.json").write_text(json.dumps({"spent_usd":.1,"requests":[{"request_id":"a","usage":{"known":True,"input_tokens":100}}]}))
            report=pilot.report_run(root)
            self.assertFalse(report["can_project"])
            self.assertIsNone(report["projection"])


if __name__ == "__main__":
    unittest.main()
