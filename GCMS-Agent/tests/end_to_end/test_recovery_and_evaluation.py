import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gcms_agent.config import load_config as _load_config, write_json

def load_config():
    config = _load_config()
    config["runtime"].update(review_strategy="legacy", budget_currency="USD", offpeak_only=False)
    return config
from gcms_agent.harness.client import HarnessError
from gcms_agent.workflow.budget import Budget
from gcms_agent.workflow.checkpoint import Checkpoints
from gcms_agent.workflow.runner import run_batch
from gcms_agent.evaluation.metrics import evaluate_results
from gcms_agent.evaluation.runner import evaluate

class KeepClient:
    def __init__(self): self.calls=0
    async def review(self,payload):
        self.calls+=1
        return {"decision":"keep","proposals":[],"summary":"保留", "usage":{"known":True,"input_tokens":100,"cache_read_input_tokens":0,"output_tokens":10}}

def sample():
    return {"sample_id":"s0","input_ids":[499],"intensity":[29],"generated":["CCO"]*10}

class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_changed_raw_intensities_cannot_resume_even_with_same_sample_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inp = root / "input.json"
            row = sample()
            write_json(inp, [row])
            config = load_config()
            await run_batch(inp, root / "run", config, mode="baseline")
            row["intensity"] = [999.0]
            write_json(inp, [row])
            with self.assertRaisesRegex(ValueError, "changed input"):
                await run_batch(inp, root / "run", config, mode="baseline", resume=True)

    async def test_settlement_idempotent_and_unknown_reconciled(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger=Budget(10,{"input":1,"cache":1,"output":1},Path(tmp)/"budget.json")
            await ledger.reserve("r",1)
            await ledger.settle("r",{"known":False})
            usage={"known":True,"input_tokens":100000,"output_tokens":0,"cache_read_input_tokens":0}
            await ledger.settle("r",usage)
            await ledger.settle("r",usage)
            self.assertAlmostEqual(ledger.ledger["spent_usd"],.1)
            self.assertEqual(len(ledger.ledger["requests"]),1)
            with self.assertRaises(ValueError):
                await ledger.settle("r",{**usage,"input_tokens":1})

    async def test_budget_increase_resumes_without_scientific_config_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); inp=root/"input.json"; write_json(inp,[sample()])
            config=load_config(); config["runtime"]["budget_usd"]=.00001
            client=KeepClient()
            await run_batch(inp,root/"run",config,client=client)
            self.assertEqual(client.calls,0)
            config["runtime"]["budget_usd"]=10
            result=await run_batch(inp,root/"run",config,resume=True,client=client)
            self.assertEqual(result["complete_samples"],1)
            self.assertEqual(client.calls,2)
            manifest=json.loads((root/"run/manifest.json").read_text())
            self.assertEqual(len(manifest["budget_revisions"]),1)

    async def test_fatal_auth_stops_queued_candidates(self):
        class Failed:
            calls=0
            async def review(self,payload):
                self.calls+=1
                raise HarnessError("auth",category="authentication_error",usage={"known":True,"input_tokens":0,"output_tokens":0})
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); row=sample(); row["generated"]=["C","CC","CCC","CCCC","CCO","CO","CCN","CN","CCOC","CCCO"]
            write_json(root/"input.json",[row]); config=load_config(); config["runtime"]["concurrency"]=1
            client=Failed()
            result=await run_batch(root/"input.json",root/"run",config,client=client)
            self.assertEqual(client.calls,1)
            self.assertEqual(result["complete_samples"],0)

    async def test_persisted_review_replayed_after_checkpoint_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); inp=root/"input.json"; write_json(inp,[sample()]); config=load_config()
            client=KeepClient(); save=Checkpoints.save
            def crash_once(instance,key,state):
                if key=="s0_c01": raise RuntimeError("simulated crash after receipt")
                return save(instance,key,state)
            with patch.object(Checkpoints,"save",crash_once):
                with self.assertRaises(RuntimeError):
                    await run_batch(inp,root/"run",config,client=client)
            self.assertEqual(client.calls,1)
            recovered=KeepClient()
            result=await run_batch(inp,root/"run",config,resume=True,client=recovered)
            self.assertEqual(result["complete_samples"],1)
            self.assertEqual(recovered.calls,1)
            ledger=json.loads((root/"run/logs/budget.json").read_text())
            self.assertEqual(len(ledger["requests"]),2)

class EvaluationTests(unittest.TestCase):
    def test_disconnected_output_not_counted_valid(self):
        result={"sample_id":"s0","unique_ranking":[],"ranked_candidates":[{"original_smiles":"CCO","final_smiles":"CC.O"}]}
        metrics=evaluate_results([result],[{"sample_id":"s0","true_smiles":"CCO"}])
        self.assertEqual(metrics["output_smiles_validity"],0)

    def test_requested_denominator_counts_missing_predictions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); write_json(root/"manifest.json",{"sample_ids":["s0","s1"]})
            write_json(root/"results/s0.json",{"sample_id":"s0","ranking_complete":True,"ranked_candidates":[],"unique_ranking":[{"final_smiles":"CCO"}]})
            write_json(root/"labels.json",[{"sample_id":s,"true_smiles":"CCO"} for s in ("s0","s1")])
            metrics=evaluate(root,root/"labels.json")
            self.assertEqual(metrics["requested_set"]["acc_at_1"],.5)
            self.assertEqual(metrics["unprocessed_samples"],1)
