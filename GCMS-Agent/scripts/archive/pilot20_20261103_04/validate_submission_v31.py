"""Retest the failed submission case; all v3 validation shares one 3 CNY cap."""
import argparse
import asyncio
import hashlib
import json
import math
from pathlib import Path
import sys
import zipfile

PROJECT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT / "src"))
from gcms_agent.config import digest, load_config, write_json
from gcms_agent.data.loader import load_inputs
from gcms_agent.workflow.runner import run_batch, source_versions
from run_economy_validation import validation_lock
from validate_reliability_v3 import prior_commitments

RUN = PROJECT / "runs/reliability_20261104_v31_submission"
CAP = 3.0


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def inventory():
    paths = [RUN / "manifest.json", RUN / "input.json", RUN / "protocol.json", RUN / "logs/budget.json"]
    paths += list((RUN / "results").glob("*.json")) + list((RUN / "checkpoints").glob("*.json"))
    return {str(p): sha(p) for p in sorted(paths)}


def combined_spend():
    spent = 0.0
    for directory in (PROJECT / "runs").glob("reliability_20261104_v3*"):
        for path in directory.glob("**/logs/budget.json"):
            ledger = read(path)
            amount = ledger["spent_cny"]
            if (ledger.get("currency") != "CNY" or not math.isfinite(amount) or amount < 0
                    or ledger.get("pending_reservations") or ledger.get("unknown_usage_sessions", 0)
                    or ledger.get("reservation_exceeded")):
                raise ValueError("Reconcile v3 accounting before another paid request")
            spent += amount
    return spent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--execute", action="store_true")
    group.add_argument("--evaluate", action="store_true")
    parser.add_argument("--sample-id", help="Local case ID; required except for --evaluate")
    args = parser.parse_args()
    if args.evaluate:
        if args.sample_id:
            parser.error("--evaluate reads the saved input; omit --sample-id")
        frozen = read(RUN / "prediction_freeze.json")
        if frozen["files"] != inventory() or frozen["script_sha256"] != sha(__file__):
            raise ValueError("Frozen outputs or evaluation script changed")
        if source_versions() != read(RUN / "manifest.json")["versions"]:
            raise ValueError("Scientific code changed after the run")
        from gcms_agent.evaluation.runner import evaluate
        result = evaluate(RUN, PROJECT / "data/evaluation/labels.json", RUN / "input.json")
        print(json.dumps({"evaluation": result, "combined_v3_spent_cny": combined_spend()}, ensure_ascii=False))
        return
    if not args.sample_id:
        parser.error("provide a local case ID with --sample-id")
    sample_id = args.sample_id
    with validation_lock(PROJECT / "runs/economy_validation.lock"):
        spent = combined_spend()
        remaining = min(CAP - spent, 27 - prior_commitments() - spent)
        if remaining <= 0:
            raise ValueError("The shared v3 validation budget is exhausted")
        if not args.execute:
            print(json.dumps({"paid_calls": False, "sample_id": sample_id, "shared_cap_cny": CAP,
                              "already_spent_cny": spent, "remaining_cny": remaining}))
            return
        if (RUN / "manifest.json").exists():
            raise ValueError("Retest already attempted; do not silently repeat it")
        row = next(r for r in load_inputs(PROJECT / "data/inference/economy_pilot.json") if r["sample_id"] == sample_id)
        config = load_config()
        config["runtime"].update(budget_currency="CNY", budget_cny=remaining, offpeak_only=True)
        write_json(RUN / "input.json", [row])
        write_json(RUN / "protocol.json", {"purpose": "One development retest after submission contract repair",
            "sample_id": sample_id, "shared_v3_budget_cny": CAP, "previously_spent_cny": spent,
            "labels_given_to_model": False, "script_sha256": sha(__file__)})
        summary = asyncio.run(run_batch(RUN / "input.json", RUN, config))
        manifest = read(RUN / "manifest.json")
        if source_versions() != manifest["versions"] or read(RUN / "logs/budget.json").get("pending_reservations"):
            raise ValueError("Run is not ready to freeze")
        with zipfile.ZipFile(RUN / "tested_source.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for rel, expected in manifest["versions"].items():
                path = PROJECT / rel
                if digest(path.read_text(encoding="utf-8")) != expected:
                    raise ValueError("Source changed before archival")
                archive.write(path, rel)
            script_path = Path(__file__).resolve()
            archive.write(script_path, script_path.relative_to(PROJECT).as_posix())
            for helper in ("validate_reliability_v3.py", "run_economy_validation.py"):
                helper_path = script_path.parent / helper
                archive.write(helper_path, helper_path.relative_to(PROJECT).as_posix())
        write_json(RUN / "prediction_freeze.json", {"files": inventory(), "script_sha256": sha(__file__),
            "source_archive_sha256": sha(RUN / "tested_source.zip"), "labels_read_during_run": False})
        print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
