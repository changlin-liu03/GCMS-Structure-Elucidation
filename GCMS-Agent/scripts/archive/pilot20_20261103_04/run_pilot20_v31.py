"""Fixed-version development regression: the original 20 cases, at most 10 CNY.

Default is a keyless preflight. No labels are read here. Paid execution retains
every session receipt, and resume is explicit and cannot raise the run ceiling.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import zipfile

PROJECT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT / "src"))
from gcms_agent.config import digest, load_config, write_json
from gcms_agent.data.loader import load_inputs
from gcms_agent.workflow.budget import tariff_at
from gcms_agent.workflow.runner import run_batch, source_versions
from run_economy_validation import validation_lock
from validate_reliability_v3 import prior_commitments
from validate_submission_v31 import combined_spend

RUN = PROJECT / "runs/pilot20_v31_20261104"
INPUT = PROJECT / "data/inference/economy_pilot.json"
SELECTION = PROJECT / "data/inference/economy_pilot.manifest.json"
CAP = 10.0


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.resume and not args.execute:
        parser.error("--resume requires --execute")
    with validation_lock(PROJECT / "runs/economy_validation.lock"):
        rows = load_inputs(INPUT)
        selected = read(SELECTION)["sample_ids"]
        if len(rows) != 20 or len(set(selected)) != 20 or [r["sample_id"] for r in rows] != selected:
            raise ValueError("Input differs from the original fixed 20-case selection")
        previous = prior_commitments() + combined_spend()
        if previous + CAP > 27:
            raise ValueError("New cap exceeds the combined validation scheduling ceiling")
        config = load_config()
        config["runtime"].update(budget_currency="CNY", budget_cny=CAP, offpeak_only=True)
        versions = source_versions()
        final = read(PROJECT / "runs/reliability_20261104_v31_submission/manifest.json")
        if versions != final["versions"]:
            raise ValueError("Scientific source differs from the tested final v31 version")
        state = {"paid_calls": bool(args.execute), "case_count": len(rows), "cap_cny": CAP,
                 "previous_validation_spend_cny": previous, "tariff": tariff_at(),
                 "model": config["model"]["model"], "thinking": config["model"]["thinking"],
                 "effort": config["model"]["effort"], "source_files": len(versions), "run": str(RUN)}
        print(json.dumps(state, ensure_ascii=False), flush=True)
        if not args.execute:
            return
        if tariff_at() != "offpeak":
            raise ValueError("Only offpeak execution is authorized by this protocol")
        if args.resume:
            protocol = read(RUN / "protocol.json")
            if protocol["script_sha256"] != sha(__file__) or protocol["source_versions"] != versions:
                raise ValueError("Launch protocol changed; refuse mixed-version resume")
            ledger = read(RUN / "logs/budget.json")
            if (ledger.get("pending_reservations") or ledger.get("unknown_usage_sessions")
                    or ledger.get("reservation_exceeded") or ledger["spent_cny"] >= CAP):
                raise ValueError("Reconcile accounting or exhausted budget before resuming")
        else:
            if RUN.exists():
                raise ValueError("Run directory exists; refuse to replace an experiment")
            write_json(RUN / "input.json", rows)
            write_json(RUN / "protocol.json", {
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "purpose": "Same original stratified 20 cases with final v31; development regression, not unseen holdout",
                "sample_ids": selected, "cap_cny": CAP, "offpeak_only": True,
                "previous_validation_spend_cny": previous, "labels_given_to_model": False,
                "input_sha256": sha(INPUT), "selection_sha256": sha(SELECTION),
                "script_sha256": sha(__file__), "source_versions": versions,
            })
            with zipfile.ZipFile(RUN / "tested_source.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for rel, expected in versions.items():
                    path = PROJECT / rel
                    if digest(path.read_text(encoding="utf-8")) != expected:
                        raise ValueError("Source changed before archival")
                    archive.write(path, rel)
                for name in ("run_pilot20_v31.py", "report_pilot20.py", "run_economy_validation.py",
                             "validate_reliability_v3.py", "validate_submission_v31.py"):
                    script_path = Path(__file__).resolve().parent / name
                    archive.write(script_path, script_path.relative_to(PROJECT).as_posix())
        summary = asyncio.run(run_batch(RUN / "input.json", RUN, config, resume=args.resume))
        if source_versions() != versions:
            raise ValueError("Scientific source changed during the experiment")
        ledger = read(RUN / "logs/budget.json")
        if ledger.get("pending_reservations"):
            raise ValueError("In-flight usage remains; cannot seal the attempt")
        write_json(RUN / "attempt.json", {
            "ended_utc": datetime.now(timezone.utc).isoformat(), "summary": summary,
            "source_unchanged": True, "labels_read": False,
            "source_archive_sha256": sha(RUN / "tested_source.zip"),
            "script_sha256": sha(__file__), "external_resume": args.resume,
        })
        print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
