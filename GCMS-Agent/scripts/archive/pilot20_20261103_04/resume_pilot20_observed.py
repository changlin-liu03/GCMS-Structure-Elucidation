"""Resume the fixed 20-case pilot, adding only redacted failure diagnostics.

The scientific source/config/skill hashes must match the original pilot.
No model inputs, tools, limits, error classification, or checkpoints are edited.
"""
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT / "src"))
from gcms_agent.config import write_json
from gcms_agent.harness.client import HarnessClient, HarnessError
from gcms_agent.harness.hooks import redact
from gcms_agent.workflow.runner import source_versions
import run_economy_validation

RUN = PROJECT / "runs/economy_20261103_optimized"
CURRENT_SAMPLE = ContextVar("pilot20_sample", default=None)


def failure_record(exc, payload):
    detail = redact(getattr(exc, "detail", None) or "")
    for name in ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        value = os.environ.get(name)
        if value:
            detail = detail.replace(value, "[REDACTED]")
    return {"utc": datetime.now(timezone.utc).isoformat(),
            "sample_id": payload.get("sample_id"),
            "candidate_ids": [c["candidate_id"] for c in payload.get("candidates", [])],
            "category": exc.category, "retryable": exc.retryable,
            "detail": detail[:800], "usage_known": exc.usage.get("known", False)}


def main():
    manifest = json.loads((RUN / "manifest.json").read_text(encoding="utf-8"))
    if source_versions() != manifest["versions"]:
        raise SystemExit("Frozen pilot source/config/skill versions changed; refusing resume")
    if manifest["config"]["runtime"]["budget_cny"] != 10:
        raise SystemExit("This observer is restricted to the original 10 CNY pilot")
    directory = RUN / "diagnostics"
    directory.mkdir(exist_ok=True)
    write_json(directory / "observer.json", {
        "scope": "redacted terminal diagnostics only; original validation runner resume",
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "scientific_versions_match": True, "budget_cny": 10,
        "private_thoughts_recorded": False,
    })
    original = HarnessClient.review_batch
    import claude_agent_sdk as sdk
    original_receive = sdk.ClaudeSDKClient.receive_response

    async def receive_observed(self):
        async for message in original_receive(self):
            if isinstance(message, sdk.ResultMessage):
                record = {"utc": datetime.now(timezone.utc).isoformat(),
                          "sample_id": CURRENT_SAMPLE.get()}
                for field in ("subtype", "stop_reason", "terminal_reason", "num_turns", "api_error_status", "is_error"):
                    value = getattr(message, field, None)
                    if value is None or type(value) in (str, int, float, bool):
                        record[field] = value
                with (directory / "terminal_metadata.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            yield message

    async def observed(self, payload):
        token = CURRENT_SAMPLE.set(payload.get("sample_id"))
        try:
            return await original(self, payload)
        except HarnessError as exc:
            with (directory / "recovery_errors.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(failure_record(exc, payload), ensure_ascii=False) + "\n")
            raise
        finally:
            CURRENT_SAMPLE.reset(token)

    HarnessClient.review_batch = observed
    sdk.ClaudeSDKClient.receive_response = receive_observed
    sys.argv = ["run_economy_validation.py", "optimized", "--resume"]
    try:
        run_economy_validation.main()
    finally:
        HarnessClient.review_batch = original
        sdk.ClaudeSDKClient.receive_response = original_receive


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
