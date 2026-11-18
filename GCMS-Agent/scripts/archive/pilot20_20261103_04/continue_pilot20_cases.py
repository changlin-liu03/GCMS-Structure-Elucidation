"""Continue the frozen 20-case pilot in single-case shards; no calls by default.

The original run and every shard share a 10 CNY accounting ceiling. Existing
results, including failures, are retained. Only --execute can start inference.
No labels, model content, or private reasoning are read by this controller.
"""
import argparse
import asyncio
import copy
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import sys
from uuid import uuid4

PROJECT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT / "src"))
from gcms_agent.config import digest, write_json
from gcms_agent.data.loader import load_inputs
from gcms_agent.harness.client import HarnessClient, HarnessError
from gcms_agent.harness.hooks import redact
from gcms_agent.workflow.runner import run_batch, scientific_config_hash, source_versions
from run_economy_validation import STAGES, validation_lock

ORIGINAL = PROJECT / "runs/economy_20261103_optimized"
INPUT = PROJECT / "data/inference/economy_pilot.json"
CAP = Decimal("10")
CURRENT = ContextVar("pilot20_continuation_session", default=None)
GLOBAL_REASONS = {"authentication_error", "billing_error", "configuration_error", "usage_error",
                  "reservation_exceeded", "budget_exhausted", "offpeak_wait"}
CASE_REASONS = {"turn_limit", "missing_submission", "timeout", "format_retry_exhausted"}
NORMAL_REASONS = {"review_complete", "objective_ceiling", "stagnation", "max_rounds", "syntax_unparseable"}
GLOBAL_HTTP = {400, 401, 402, 403, 408, 429, 500, 502, 503, 504, 529}


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def money(value):
    if type(value) not in (int, float, Decimal):
        raise ValueError("Ledger amount must be numeric")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Invalid ledger amount") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("Invalid ledger amount")
    return result


def ledger_commitment(ledger):
    """Count spent plus pending exactly once; return blockers without hiding cost."""
    if ledger.get("currency") != "CNY":
        raise ValueError("Only CNY ledgers are permitted")
    spent = money(ledger.get("spent_cny", 0))
    pending = ledger.get("pending_reservations", {})
    requests = ledger.get("requests", [])
    identifiers = [item["request_id"] for item in requests]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Duplicate request IDs within a ledger")
    if set(identifiers) & set(pending):
        raise ValueError("A request is both settled and pending")
    if abs(sum((money(item["cost_cny"]) for item in requests), Decimal(0)) - spent) > Decimal("0.000000001"):
        raise ValueError("Ledger spend does not equal request receipts")
    blockers = []
    if pending:
        blockers.append("pending_reservations")
    if any(item.get("usage", {}).get("known") is not True for item in requests):
        blockers.append("unknown_usage")
    if ledger.get("reservation_exceeded") or any(item.get("reservation_exceeded") for item in requests):
        blockers.append("reservation_exceeded")
    return spent + sum((money(value) for value in pending.values()), Decimal(0)), blockers


def accounting(run, shard_dirs):
    if not (run / "logs/budget.json").is_file():
        raise ValueError("Original pilot ledger is missing; previous costs cannot be assumed zero")
    paths = [run / "logs/budget.json", *(path / "logs/budget.json" for path in shard_dirs)]
    total, other, blockers, entries, seen = Decimal(0), Decimal(0), [], [], set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            raise ValueError("Duplicate ledger path")
        seen.add(resolved)
        if not path.exists():
            continue
        amount, issues = ledger_commitment(read_json(path))
        total += amount
        entries.append({"path": str(resolved), "committed_cny": float(amount), "blockers": issues})
        blockers.extend(f"{path.parent.parent.name}:{issue}" for issue in issues)
    # Preserve the broader validation schedule's existing 27 CNY ceiling too.
    for stage in STAGES:
        if stage == "optimized":
            continue
        path = PROJECT / "runs" / f"economy_20261103_{stage}" / "logs/budget.json"
        if path.exists():
            amount, issues = ledger_commitment(read_json(path))
            other += amount
            blockers.extend(f"other_stage_{stage}:{issue}" for issue in issues)
    remaining = min(CAP - total, Decimal("27") - total - other)
    return {"cap_cny": 10, "committed_cny": float(total), "remaining_cny": float(max(remaining, Decimal(0))),
            "other_validation_committed_cny": float(other), "blockers": blockers, "ledgers": entries}, remaining


def latest_records(directory, filename, sample_id):
    path = directory / filename
    if not path.exists():
        return None
    latest = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            if item.get("sample_id") == sample_id:
                latest = item
    return latest


def classify_result(result, terminal=None, failure=None):
    """Classify continuation policy separately; never rewrite scientific results."""
    candidates = result.get("ranked_candidates", [])
    if not candidates:
        return "stop", "missing_candidate_results"
    reasons = {item.get("stop_reason") for item in candidates}
    if reasons & GLOBAL_REASONS:
        return "stop", ",".join(sorted(reasons & GLOBAL_REASONS))
    terminal, failure = terminal or {}, failure or {}
    if terminal.get("api_error_status") in GLOBAL_HTTP:
        return "stop", "global_api_error"
    if failure.get("category") in GLOBAL_REASONS:
        return "stop", failure["category"]
    if "service_error" in reasons:
        # Counts alone (e.g. 4 * 4096) are not evidence of truncation.
        detail = failure.get("detail", "").lower()
        explicit_limit = terminal.get("stop_reason") == "max_tokens" or bool(re.search(
            r"response exceeded the \d+ output token maximum|"
            r"max[_ -]?output[_ -]?tokens|max_tokens|output (?:token )?limit|maximum output|output token budget", detail))
        same_session = (not terminal.get("session_key") or not failure.get("session_key")
                        or terminal["session_key"] == failure["session_key"])
        terminal_shape = (terminal.get("subtype") == "error_during_execution"
                          or (terminal.get("subtype") == "success" and terminal.get("terminal_reason") == "api_error"))
        if (terminal.get("is_error") is True and terminal_shape
                and explicit_limit and same_session and not failure.get("retryable")):
            return "continue", "case_output_truncated"
        return "stop", "unclassified_service_or_transport_error"
    unknown = reasons - NORMAL_REASONS - CASE_REASONS
    if unknown:
        return "stop", "unclassified_case_stop"
    if result.get("ranking_complete"):
        return "continue", "complete"
    if reasons & CASE_REASONS:
        return "continue", ",".join(sorted(reasons & CASE_REASONS))
    return "stop", "unclassified_incomplete_result"


def inspect_existing(rows, run, continuation):
    records, shard_dirs, blockers = [], [], []
    known = {row["sample_id"] for row in rows}
    shard_root = continuation / "shards"
    if shard_root.exists():
        unexpected = [path.name for path in shard_root.iterdir() if path.is_dir() and path.name not in known]
        if unexpected:
            raise ValueError("Unknown case directory in frozen pilot shards")
    for row in rows:
        sid = row["sample_id"]
        shard = shard_root / sid
        if shard.exists():
            shard_dirs.append(shard)
        original_result = run / "results" / f"{sid}.json"
        shard_result = shard / "results" / f"{sid}.json"
        if original_result.exists() and shard_result.exists():
            raise ValueError("Case has both an original and a shard result; refuse ambiguous resampling")
        path = original_result if original_result.exists() else shard_result
        if path.exists():
            result = read_json(path)
            if result.get("sample_id") != sid or result.get("input_hash") != digest(row):
                raise ValueError("Saved result does not match its frozen input")
            owner = run if path == original_result else shard
            terminal = latest_records(owner / "diagnostics", "terminal_metadata.jsonl", sid)
            failure = latest_records(owner / "diagnostics", "recovery_errors.jsonl", sid)
            policy, reason = classify_result(result, terminal, failure)
            records.append({"sample_id": sid, "status": "complete" if result.get("ranking_complete") else "failed",
                            "result_path": str(path.resolve()), "continuation_policy": policy, "reason": reason})
            if policy == "stop":
                blockers.append(f"{sid}:{reason}")
        elif (shard / "attempt.json").exists() or (shard / "manifest.json").exists() or (shard / "logs/budget.json").exists():
            records.append({"sample_id": sid, "status": "interrupted", "result_path": None})
            blockers.append(f"{sid}:attempt_without_result_requires_reconciliation")
        else:
            records.append({"sample_id": sid, "status": "not_attempted", "result_path": None})
    return records, shard_dirs, blockers


def verify_frozen(run, input_path):
    original = read_json(run / "manifest.json")
    rows = load_inputs(input_path)
    if len(rows) != 20 or [row["sample_id"] for row in rows] != original.get("sample_ids"):
        raise ValueError("Input must be the original ordered 20-case pilot")
    if digest(rows) != original.get("input_hash") or source_versions() != original.get("versions"):
        raise ValueError("Frozen scientific code/skills/configs or input changed")
    runtime = original["config"]["runtime"]
    if original.get("backend") != "claude_agent_sdk" or original.get("mode") != "agent":
        raise ValueError("Original pilot must use the live Claude Agent SDK backend")
    if runtime.get("budget_currency") != "CNY" or money(runtime["budget_cny"]) != CAP:
        raise ValueError("Original pilot must have the 10 CNY ceiling")
    return original, rows


def verify_shards(original, rows, continuation):
    for row in rows:
        shard = continuation / "shards" / row["sample_id"]
        if (shard / "input.json").exists() and load_inputs(shard / "input.json") != [row]:
            raise ValueError("Shard input differs from frozen case")
        if (shard / "manifest.json").exists():
            if not (shard / "logs/budget.json").exists():
                raise ValueError("Started shard has no budget ledger; reconcile before continuing")
            manifest = read_json(shard / "manifest.json")
            if (manifest.get("versions") != original["versions"] or manifest.get("input_hash") != digest([row])
                    or manifest.get("backend") != "claude_agent_sdk" or manifest.get("mode") != "agent"
                    or scientific_config_hash(manifest["config"]) != scientific_config_hash(original["config"])):
                raise ValueError("Shard scientific identity differs from original pilot")


def append_record(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
        handle.flush()


@contextmanager
def observed_sessions(directory):
    import claude_agent_sdk as sdk
    original_review = HarnessClient.review_batch
    original_receive = sdk.ClaudeSDKClient.receive_response

    async def receive_observed(self):
        async for message in original_receive(self):
            if isinstance(message, sdk.ResultMessage):
                record = {"utc": now(), **(CURRENT.get() or {})}
                for field in ("subtype", "stop_reason", "terminal_reason", "num_turns", "api_error_status", "is_error"):
                    value = getattr(message, field, None)
                    if value is None or type(value) in (str, int, float, bool):
                        record[field] = value
                append_record(directory / "terminal_metadata.jsonl", record)
            yield message

    async def review_observed(self, payload):
        identity = {"sample_id": payload.get("sample_id"), "session_key": uuid4().hex}
        token = CURRENT.set(identity)
        try:
            return await original_review(self, payload)
        except HarnessError as exc:
            append_record(directory / "recovery_errors.jsonl", {
                "utc": now(), **identity, "category": exc.category, "retryable": exc.retryable,
                "detail": redact(exc.detail or "")[:800], "usage_known": exc.usage.get("known", False)})
            raise
        finally:
            CURRENT.reset(token)

    HarnessClient.review_batch = review_observed
    sdk.ClaudeSDKClient.receive_response = receive_observed
    try:
        yield
    finally:
        HarnessClient.review_batch = original_review
        sdk.ClaudeSDKClient.receive_response = original_receive


def snapshot(original, rows, run, continuation):
    verify_shards(original, rows, continuation)
    records, shard_dirs, blockers = inspect_existing(rows, run, continuation)
    budget, remaining = accounting(run, shard_dirs)
    blockers.extend(budget["blockers"])
    if remaining <= 0:
        blockers.append("aggregate_budget_exhausted")
    summary = {"utc": now(), "requested_samples": 20, "sample_ids": original["sample_ids"],
               "complete_samples": sum(item["status"] == "complete" for item in records),
               "failed_samples": sum(item["status"] == "failed" for item in records),
               "unattempted_samples": sum(item["status"] == "not_attempted" for item in records),
               "interrupted_samples": sum(item["status"] == "interrupted" for item in records),
               "cases": records, "budget": budget, "blockers": blockers,
               "result_policy": "Original results and all failure receipts retained; no resampling; denominator 20"}
    return summary, remaining


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--execute", action="store_true", help="Explicitly start remaining paid cases")
    choice.add_argument("--self-test", action="store_true", help="Run keyless helper checks; no files or API calls")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    # The same OS lock is held throughout all shards, including every paid call.
    with validation_lock(PROJECT / "runs/economy_validation.lock"):
        original, rows = verify_frozen(ORIGINAL, INPUT)
        continuation = ORIGINAL / "continuation"
        summary, remaining = snapshot(original, rows, ORIGINAL, continuation)
        if not args.execute:
            print(json.dumps({"dry_run": True, **summary}, ensure_ascii=False, indent=2))
            return
        script_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        index = continuation / "manifest.json"
        identity = {"source_run": str(ORIGINAL.resolve()), "input_hash": original["input_hash"],
                    "scientific_config_hash": scientific_config_hash(original["config"]),
                    "versions": original["versions"], "sample_ids": original["sample_ids"],
                    "cap_cny": 10, "controller_sha256": script_hash}
        if index.exists():
            if read_json(index).get("identity") != identity:
                raise ValueError("Continuation controller or frozen identity changed")
        else:
            write_json(index, {"identity": identity, "created_utc": now(), "private_thoughts_recorded": False})
        write_json(continuation / "summary.json", summary)
        if summary["blockers"]:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return
        for row in rows:
            # Re-read every ledger and result immediately before a new case.
            if source_versions() != original["versions"]:
                raise ValueError("Scientific files changed during continuation")
            summary, remaining = snapshot(original, rows, ORIGINAL, continuation)
            write_json(continuation / "summary.json", summary)
            if summary["blockers"]:
                break
            existing = next(item for item in summary["cases"] if item["sample_id"] == row["sample_id"])
            if existing["status"] != "not_attempted":
                continue
            shard = continuation / "shards" / row["sample_id"]
            config = copy.deepcopy(original["config"])
            # Truncate rather than round upward when converting decimal funds.
            limit = remaining.quantize(Decimal("0.00000001"), rounding="ROUND_DOWN")
            if limit <= 0:
                break
            config["runtime"]["budget_cny"] = float(limit)
            if scientific_config_hash(config) != scientific_config_hash(original["config"]):
                raise ValueError("Unexpected scientific configuration change")
            write_json(shard / "input.json", [row])
            write_json(shard / "attempt.json", {"sample_id": row["sample_id"], "started_utc": now(),
                "budget_cny": float(limit), "committed_before_cny": summary["budget"]["committed_cny"],
                "controller_sha256": script_hash, "resampling_permitted": False})
            try:
                with observed_sessions(shard / "diagnostics"):
                    asyncio.run(run_batch(shard / "input.json", shard, config, mode="agent", client=None))
            except BaseException as exc:
                # No exception strings: arbitrary SDK/OS failures can carry secrets.
                write_json(shard / "controller_error.json", {"utc": now(), "exception_type": type(exc).__name__,
                                                            "policy": "stop_for_reconciliation"})
                summary, _ = snapshot(original, rows, ORIGINAL, continuation)
                write_json(continuation / "summary.json", summary)
                raise
        summary, _ = snapshot(original, rows, ORIGINAL, continuation)
        write_json(continuation / "summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))


def self_test():
    known = {"currency": "CNY", "spent_cny": .2, "requests": [
        {"request_id": "r1", "cost_cny": .2, "usage": {"known": True}}], "pending_reservations": {}}
    assert ledger_commitment(known) == (Decimal("0.2"), [])
    unknown = copy.deepcopy(known)
    unknown["requests"][0]["usage"]["known"] = False
    assert "unknown_usage" in ledger_commitment(unknown)[1]
    pending = copy.deepcopy(known)
    pending["pending_reservations"] = {"r2": .3}
    assert ledger_commitment(pending) == (Decimal("0.5"), ["pending_reservations"])
    overrun = copy.deepcopy(known)
    overrun["requests"][0]["reservation_exceeded"] = True
    assert "reservation_exceeded" in ledger_commitment(overrun)[1]
    failed = {"ranking_complete": False, "ranked_candidates": [{"stop_reason": "service_error"}]}
    assert classify_result(failed)[0] == "stop"
    terminal = {"subtype": "error_during_execution", "is_error": True, "stop_reason": "max_tokens"}
    assert classify_result(failed, terminal, {"retryable": False}) == ("continue", "case_output_truncated")
    observed = {"subtype": "success", "is_error": True, "stop_reason": "stop_sequence",
                "terminal_reason": "api_error", "api_error_status": None}
    limit_error = {"retryable": False, "detail": "API Error: Claude's response exceeded the 4096 output token maximum."}
    assert classify_result(failed, observed, limit_error) == ("continue", "case_output_truncated")
    assert classify_result(failed, observed, {"detail": "Unknown provider error"})[0] == "stop"
    assert classify_result(failed, {**terminal, "api_error_status": 429})[0] == "stop"
    assert classify_result(failed, {**terminal, "session_key": "a"}, {"session_key": "b"})[0] == "stop"
    assert classify_result({"ranking_complete": False, "ranked_candidates": [{"stop_reason": "turn_limit"}]})[0] == "continue"
    assert classify_result({"ranking_complete": False, "ranked_candidates": [{"stop_reason": "configuration_error"}]})[0] == "stop"
    assert Decimal("0.123456789").quantize(Decimal("0.00000001"), rounding="ROUND_DOWN") == Decimal("0.12345678")
    print("PASS: keyless accounting, truncation classification, and no-resampling policy helpers")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
