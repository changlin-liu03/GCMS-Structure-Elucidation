"""Replay existing recorded proposals through new tools/workflow, without an API.

This checks protocol and deterministic result preservation, not new model quality
or token savings. It never loads evaluation labels or case_analysis.json.
"""
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
from gcms_agent.config import PROJECT_ROOT, load_config, write_json
from gcms_agent.data.loader import load_inputs
from gcms_agent.data.intensity import raw_peaks
from gcms_agent.data.deduplication import candidate_key
from gcms_agent.harness.skill_loader import SkillLibrary
from gcms_agent.harness.prompts import user_prompt, batch_user_prompt
from gcms_agent.tools.batch_server import BatchReviewTools
from gcms_agent.workflow.case import process_case
from gcms_agent.workflow.budget import Budget
from gcms_agent.workflow.checkpoint import Checkpoints


class RecordedReviewer:
    def __init__(self, source):
        self.source = source
        self.library = SkillLibrary(PROJECT_ROOT)
        self.calls = 0
        self.candidate_reviews = 0
        self.legacy_chars = self.batch_chars = 0

    async def review_batch(self, payload):
        self.calls += 1
        self.batch_chars += len(batch_user_prompt(payload))
        state = BatchReviewTools(payload, self.library)
        reviews = []
        for candidate in payload["candidates"]:
            self.candidate_reviews += 1
            self.legacy_chars += len(user_prompt({**candidate, "sample_id": payload["sample_id"], "peaks": payload["peaks"]}))
            name = f"{payload['sample_id']}_{candidate['candidate_id']}_r{candidate['round']}_review.json"
            recorded = json.loads((self.source / "checkpoints" / name).read_text(encoding="utf-8"))["review"]
            references = []
            if recorded["proposals"]:
                response = state.analyze({"proposals": [{"candidate_id": candidate["candidate_id"],
                    "base_version": item["base_version"], "operations": item["operations"]}
                    for item in recorded["proposals"]]})
                for original, trial in zip(recorded["proposals"], response["results"]):
                    references.append({"trial_id": trial["trial_id"], **{key: original[key] for key in
                                      ("reason", "peak_refs", "operation_explanations")}})
            reviews.append({"candidate_id": candidate["candidate_id"], "decision": recorded["decision"],
                            "proposals": references, "summary": recorded["summary"]})
        state.submit({"reviews": reviews})
        return {**state.submitted, "usage": {"known": True, "input_tokens": 0,
                "cache_read_input_tokens": 0, "output_tokens": 0}, "format_errors": 0,
                "actual_models": ["offline-recorded-replay-no-api"], "audit": state.audit()}


async def main():
    from tempfile import TemporaryDirectory
    source = PROJECT_ROOT / "runs/single_top10_miss_nothinking_v2"
    row = load_inputs(PROJECT_ROOT / "data/inference/single_top10_miss.json")[0]
    peaks = raw_peaks(row["input_ids"], row["intensity"])
    jobs = {}
    for index, smiles in enumerate(row["generated"]):
        jobs.setdefault(candidate_key(smiles), (f"c{index+1:02d}", smiles))
    config = load_config()
    reviewer = RecordedReviewer(source)
    with TemporaryDirectory(prefix="gcms-economy-replay-") as temp:
        budget = Budget(10, config["model"]["prices"], Path(temp)/"budget.json")
        states = await process_case(row, list(jobs.values()), peaks, config, reviewer, budget,
                                    Checkpoints(Path(temp)/"checkpoints"))
    checks = []
    for state in states:
        original = json.loads((source / "checkpoints" / f"{row['sample_id']}_{state['candidate_id']}.json").read_text(encoding="utf-8"))
        same = state["final_smiles"] == original["final_smiles"] and state["score"] == original["score"]
        if not same:
            raise AssertionError(f"Replay changed deterministic result: {state['candidate_id']}")
        checks.append({"candidate_id": state["candidate_id"], "same_final_structure_and_score": same,
                       "final_valid": state["validation"]["valid"], "rounds": state["rounds_completed"],
                       "accepted_edits": state["accepted_edits"]})
    result = {"uses_api": False, "uses_evaluation_labels": False,
              "scope": "Recorded formal-proposal replay; not a live accuracy or cost benchmark",
              "sample_id": row["sample_id"], "source_slots": len(row["generated"]),
              "unique_candidates": len(states), "batch_calls": reviewer.calls,
              "candidate_reviews": reviewer.candidate_reviews, "checks": checks,
              "equivalent_separate_prompt_chars": reviewer.legacy_chars,
              "batch_prompt_chars": reviewer.batch_chars,
              "character_reduction": 1-reviewer.batch_chars/reviewer.legacy_chars,
              "token_saving": None, "live_cost": None}
    write_json(PROJECT_ROOT / "runs/economy_offline_validation/replay.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
