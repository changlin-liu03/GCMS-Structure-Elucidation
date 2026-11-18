"""One real Claude-Harness/DeepSeek tool roundtrip; never print credentials."""
import asyncio
import json
from gcms_agent.config import PROJECT_ROOT, load_config, write_json
from gcms_agent.chemistry.smiles_io import parse_smiles
from gcms_agent.data.intensity import raw_peaks
from gcms_agent.workflow.candidate import assess
from gcms_agent.harness.client import HarnessClient

async def main():
    from gcms_agent.credentials import load_project_credentials
    load_project_credentials()
    config = load_config()
    parsed = parse_smiles("CCO")
    peaks = raw_peaks([31,45,46], [29,20,18])
    validation, evidence, score = assess(parsed["graph"], peaks)
    payload = {"sample_id":"probe_ethanol", "candidate_id":"c01", "graph":parsed["graph"], "molecode":parsed["molecode"], "validation":validation, "evidence":evidence, "score":score, "round":1, "previous_feedback":[], "peaks":peaks, "graph_version":0, "format_repairs_remaining":2}
    client = HarnessClient({**config["model"], "max_output_tokens":8192, "max_tool_turns":8}, PROJECT_ROOT)
    try:
        result = await client.review(payload)
        write_json(PROJECT_ROOT/"runs/probe/result.json", result)
        print(json.dumps({"ok":True, "decision":result["decision"], "usage":result["usage"], "actual_models":result.get("actual_models",[])}, ensure_ascii=False))
    except Exception as exc:
        output = {"ok":False, "type":type(exc).__name__, "category":getattr(exc,"category","error"), "message":str(exc), "usage":getattr(exc,"usage",{})}
        write_json(PROJECT_ROOT/"runs/probe/result.json", output)
        print(json.dumps(output, ensure_ascii=False))
        raise SystemExit(1)

if __name__ == "__main__":
    asyncio.run(main())
