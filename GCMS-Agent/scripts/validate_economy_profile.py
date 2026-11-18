"""Bounded, label-free live engineering pilot. Never saves credentials."""
import argparse
import asyncio
from collections import Counter
import getpass
import json
import os
from pathlib import Path
import sys
import time
import warnings

from gcms_agent.config import DEFAULT_DATA_ROOT, load_config, write_json
from gcms_agent.data.loader import load_inputs
from gcms_agent.workflow.runner import run_batch



def report(output, elapsed):
    ledger = json.loads((output / "logs/budget.json").read_text(encoding="utf-8"))
    cases = []
    for path in sorted((output / "results").glob("*.json")):
        result = json.loads(path.read_text(encoding="utf-8"))
        candidates = result["ranked_candidates"]
        cases.append({"sample_id": result["sample_id"], "complete": result["ranking_complete"],
            "valid_slots": sum(c.get("validation", {}).get("valid", False) and bool(c.get("final_smiles")) for c in candidates),
            "stop_reasons": dict(Counter(c["stop_reason"] for c in candidates)),
            "resource_usage": result["resource_usage"]})
    sessions = []
    for path in sorted((output / "checkpoints").glob("*.json")):
        item = json.loads(path.read_text(encoding="utf-8"))
        if "request_id" not in item:
            continue
        review = item.get("review", item)
        audit = review.get("audit", {})
        if not audit:
            continue
        watchdog = audit.get("progress_watchdog", {})
        sessions.append({"request_id": item["request_id"], "elapsed_seconds": item.get("elapsed_seconds"),
            "failure": item.get("error_category"), "thinking": audit.get("thinking_control"),
            "first_tool_seconds": watchdog.get("first_tool_seconds"),
            "guard_trigger": watchdog.get("trigger"), "usage": review.get("usage")})
    result = {"scope": "Four previously used development cases; label-free engineering pilot, not accuracy evaluation",
        "paid_api": True, "total_wall_seconds": elapsed, "cases": cases, "sessions": sessions,
        "known_cost_cny": sum(r["cost_cny"] for r in ledger["requests"] if r["usage"].get("known")),
        "estimated_unknown_cost_cny": sum(r["cost_cny"] for r in ledger["requests"] if not r["usage"].get("known")),
        "scheduling_spend_cny": ledger["spent_cny"], "pending_reservations": ledger["pending_reservations"],
        "full_dataset_projection": None,
        "projection_note": "Small development pilot cannot establish 1490-case completion, accuracy or cost."}
    write_json(output / "engineering_report.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--ask-key", action="store_true", help="Read key with terminal echo disabled, for this process only")
    parser.add_argument("--budget-cny", type=float, help="Optional monetary cap; otherwise follow project configuration")
    args = parser.parse_args()
    import math
    if args.budget_cny is not None and (not math.isfinite(args.budget_cny) or args.budget_cny <= 0):
        parser.error("Budget must be finite and positive")
    if (args.output / "manifest.json").exists():
        parser.error("Use a new output directory; this pilot never silently resumes paid work")
    config = load_config(profile="economy")
    if args.budget_cny is not None:
        config["runtime"]["budget_cny"] = args.budget_cny
    inputs = load_inputs(args.data_root / "inference/engineering_pilot.json")
    path = args.output / "input.json"
    write_json(path, inputs)
    from gcms_agent.credentials import load_project_credentials
    load_project_credentials()
    prompted = False
    if args.ask_key and not os.environ.get("DEEPSEEK_API_KEY"):
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            key = getpass.getpass("API key (hidden, process only): ").strip()
        if not key:
            parser.error("No key provided")
        os.environ["DEEPSEEK_API_KEY"] = key
        del key
        prompted = True
    if not os.environ.get("DEEPSEEK_API_KEY"):
        parser.error("Set DEEPSEEK_API_KEY or use --ask-key; do not put a key in command arguments")
    started = time.monotonic()
    try:
        summary = asyncio.run(run_batch(path, args.output, config))
        result = report(args.output, time.monotonic() - started)
        print(json.dumps({"summary": summary, "wall_seconds": result["total_wall_seconds"],
                          "report": str(args.output / "engineering_report.json")}, ensure_ascii=False))
    finally:
        if prompted:
            os.environ.pop("DEEPSEEK_API_KEY", None)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
