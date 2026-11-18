"""Explicit, bounded paid validation stages. Credentials stay in process env."""
import argparse
import asyncio
import json
import math
import os
from contextlib import contextmanager
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
from gcms_agent.config import PROJECT_ROOT, load_config
from gcms_agent.workflow.runner import run_batch

STAGES = {
    "regression": ("single_top10_miss.json", "case_batch", 2.0),
    "regression_v2": ("single_top10_miss.json", "case_batch", 1.0),
    "regression_v3": ("single_top10_miss.json", "case_batch", 1.0),
    "regression_low": ("single_top10_miss.json", "case_batch", 1.0),
    "optimized": ("economy_pilot.json", "case_batch", 10.0),
    "legacy": ("economy_paired5.json", "legacy", 15.0),
}


def validation_budget(stage, ledgers):
    """Return the phase's total ceiling after all other commitments, in CNY."""
    if stage not in STAGES:
        raise ValueError("Unknown validation stage")
    committed = {}
    for name, ledger in ledgers.items():
        if name not in STAGES or ledger.get("currency") != "CNY":
            raise ValueError("Validation requires recognized CNY stage ledgers")
        pending = ledger.get("pending_reservations", {})
        if name != stage and pending:
            raise ValueError("Another stage has unresolved/in-flight reservations; reconcile it first")
        amounts = [ledger.get("spent_cny", 0), *pending.values()]
        if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0 for value in amounts):
            raise ValueError("Invalid validation ledger amount")
        committed[name] = sum(amounts)
    ceiling = min(STAGES[stage][2], 27.0 - sum(value for name, value in committed.items() if name != stage))
    current = ledgers.get(stage, {})
    if ceiling <= committed.get(stage, 0):
        raise ValueError("Combined validation budget exhausted; no new paid calls started")
    if current.get("limit_cny", ceiling) > ceiling:
        raise ValueError("Remaining combined budget is below the saved phase limit; cannot resume this phase safely")
    return ceiling


@contextmanager
def validation_lock(path):
    """OS-held lock prevents simultaneous stages; automatically releases on exit."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if handle.seek(0, 2) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("Another validation process holds the shared budget lock") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=STAGES)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    name, strategy, _ = STAGES[args.stage]
    # All phases share the 27 CNY scheduling ceiling, leaving 3 CNY of the
    # authorized 30 for in-flight overrun. Versioned regression runs retain
    # their independent receipts and are included in the same total budget.
    with validation_lock(PROJECT_ROOT / "runs/economy_validation.lock"):
        ledgers = {}
        for stage in STAGES:
            path = PROJECT_ROOT / "runs" / f"economy_20261103_{stage}" / "logs/budget.json"
            if path.exists():
                ledgers[stage] = json.loads(path.read_text(encoding="utf-8"))
        ceiling = validation_budget(args.stage, ledgers)
        config = load_config()
        config["runtime"].update(review_strategy=strategy, budget_currency="CNY", budget_cny=ceiling,
                                 offpeak_only=True)
        if strategy == "legacy":
            config["runtime"].update(max_tool_turns=12, max_output_tokens=8192)
        output = PROJECT_ROOT / "runs" / f"economy_20261103_{args.stage}"
        result = asyncio.run(run_batch(PROJECT_ROOT / "data/inference" / name, output, config,
                                      resume=args.resume))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
