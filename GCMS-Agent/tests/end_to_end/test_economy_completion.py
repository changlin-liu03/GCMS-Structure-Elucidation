import asyncio
import json
from gcms_agent.config import load_config, write_json
from gcms_agent.workflow.runner import run_batch


def test_unexplored_round_exhaustion_is_incomplete(tmp_path):
    class NoExploration:
        async def review_batch(self, payload):
            return {"reviews": [{"candidate_id": c["candidate_id"], "decision": "keep",
                                 "proposals": [], "summary": "No verified exploration"}
                                for c in payload["candidates"]],
                    "usage": {"known": True, "input_tokens": 1, "output_tokens": 1}}

    config = load_config(profile="economy")
    config["runtime"]["offpeak_only"] = False
    source = tmp_path / "input.json"
    write_json(source, [{"sample_id": "bounded", "input_ids": [499], "intensity": [29],
                         "generated": ["CC"] * 10}])
    summary = asyncio.run(run_batch(source, tmp_path / "run", config, client=NoExploration()))
    result = json.loads((tmp_path / "run/results/bounded.json").read_text(encoding="utf-8"))
    assert summary["complete_samples"] == 0
    assert result["ranking_complete"] is False
    assert all(c["stop_reason"] == "max_rounds" for c in result["ranked_candidates"])
