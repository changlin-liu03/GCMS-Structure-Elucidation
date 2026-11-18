import asyncio
import copy
import json
import platform
import time
from datetime import datetime, timezone
from pathlib import Path
from rdkit import rdBase, RDLogger
from gcms_agent.config import PROJECT_ROOT, digest, write_json
from gcms_agent.data.loader import load_inputs
from gcms_agent.data.intensity import peaks_from_input
from gcms_agent.data.deduplication import candidate_key
from gcms_agent.harness.client import HarnessClient
from gcms_agent.scoring.ranking import rank_candidates
from gcms_agent.workflow.budget import Budget
from gcms_agent.workflow.checkpoint import Checkpoints
from gcms_agent.workflow.candidate import process_candidate
from gcms_agent.workflow.case import process_case
from gcms_agent.reporting.export import export_sample

RESOURCE_STOPS = {"budget_exhausted", "offpeak_wait", "reservation_exceeded", "turn_limit", "output_limit", "timeout", "configuration_error", "service_error", "tool_error", "format_retry_exhausted", "usage_error", "missing_submission", "authentication_error", "billing_error"}
RESOURCE_STOPS |= {"pre_tool_no_progress", "session_output_limit", "case_budget_exhausted", "case_session_limit", "max_rounds"}
RESOURCE_STOPS.add("case_transport_failure_limit")

def scientific_config_hash(config):
    frozen = copy.deepcopy(config)
    frozen["runtime"].pop("budget_usd", None)
    frozen["runtime"].pop("budget_cny", None)
    frozen["runtime"].pop("reservation_input_tokens", None)
    return digest(frozen)

def source_versions():
    result = {}
    for folder in ("src", "skills", "resources", "configs"):
        for path in (PROJECT_ROOT/folder).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix not in (".pyc",):
                result[str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")] = digest(path.read_text(encoding="utf-8"))
    return result


def sample_resource_usage(budget, sample_id, runtime):
    """Aggregate session receipts once, never candidate lineage copies."""
    requests = [entry for entry in budget.ledger["requests"] if entry.get("sample_id") == sample_id]
    totals = {field: sum(entry["usage"].get(field, 0) for entry in requests if entry["usage"].get("known"))
              for field in ("input_tokens", "cache_read_input_tokens", "output_tokens")}
    target = runtime.get("case_output_token_target", 25000)
    return {**totals, "sessions": len(requests),
            "effective_sessions": sum(not entry.get("excluded_transport_failure", False) for entry in requests),
            "excluded_transport_failures": sum(entry.get("excluded_transport_failure", False) for entry in requests),
            "unknown_usage_sessions": sum(not entry["usage"].get("known", False) for entry in requests),
            "currency": budget.currency, f"cost_{budget.currency.lower()}": sum(entry.get(budget.cost_key, 0) for entry in requests),
            "output_token_target": target, "output_token_target_exceeded": totals["output_tokens"] > target,
            "target_policy": "diagnostic only; never truncate edits or explanations"}

async def run_batch(input_path, run_dir, config, mode="agent", limit=None, resume=False, client=None):
    RDLogger.DisableLog("rdApp.error")
    backend = ("claude_agent_sdk" if client is None else "injected_client") if mode == "agent" else "deterministic_baseline"
    rows = load_inputs(input_path)
    if limit is not None:
        rows = rows[:limit]
    run_dir = Path(run_dir).resolve()
    manifest_path = run_dir / "manifest.json"
    input_hash = digest(rows)
    if manifest_path.exists():
        if not resume:
            raise ValueError("Run exists; use resume or choose a new output directory")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("backend") != backend:
            raise ValueError("Cannot resume with a different or unrecorded inference backend")
        if manifest["input_hash"] != input_hash or scientific_config_hash(manifest["config"]) != scientific_config_hash(config) or manifest["mode"] != mode or manifest["versions"] != source_versions():
            raise ValueError("Cannot resume with changed input, config, mode, or scientific code/skills")
        currency = config["runtime"].get("budget_currency", "USD").lower()
        budget_key = f"budget_{currency}"
        old_budget = manifest.get(f"active_{budget_key}", manifest["config"]["runtime"][budget_key])
        new_budget = config["runtime"][budget_key]
        old_reservation = manifest.get("active_reservation_input_tokens", manifest["config"]["runtime"].get("reservation_input_tokens", 80000))
        new_reservation = config["runtime"].get("reservation_input_tokens", 80000)
        if new_reservation < old_reservation:
            raise ValueError("A resumed run may only retain or increase its input reservation")
        if new_budget is not None and (old_budget is None or new_budget < old_budget):
            raise ValueError("A resumed run may only retain or increase its budget")
        if new_budget != old_budget:
            manifest.setdefault("budget_revisions", []).append({f"old_{currency}":old_budget, f"new_{currency}":new_budget, "utc":datetime.now(timezone.utc).isoformat()})
            manifest[f"active_{budget_key}"] = new_budget
            write_json(manifest_path, manifest)
        if new_reservation > old_reservation:
            manifest.setdefault("resource_revisions", []).append({"field": "reservation_input_tokens",
                "old": old_reservation, "new": new_reservation, "utc": datetime.now(timezone.utc).isoformat()})
            manifest["active_reservation_input_tokens"] = new_reservation
            write_json(manifest_path, manifest)
    else:
        manifest = {"run_id": run_dir.name, "created_utc": datetime.now(timezone.utc).isoformat(), "input_hash": input_hash,
                    "config_hash": digest(config), "config": config, "mode": mode, "backend": backend, "versions": source_versions(),
                    "python": platform.python_version(), "rdkit": rdBase.rdkitVersion, "sample_ids": [r["sample_id"] for r in rows]}
        write_json(manifest_path, manifest)
    ledger_path = run_dir / "logs" / "budget.json"
    existing = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else None
    budget = Budget.from_config(config, ledger_path, existing)
    checkpoints = Checkpoints(run_dir / "checkpoints")
    if client is None and mode == "agent":
        from gcms_agent.credentials import load_project_credentials
        load_project_credentials()
        client = HarnessClient({**config["model"], "max_output_tokens": config["runtime"]["max_output_tokens"],
                               "max_tool_turns": config["runtime"].get("max_tool_turns", 6),
                               "reserve_submit_turns": config["runtime"].get("reserve_submit_turns", 1),
                               "max_trial_batches": config["runtime"].get("max_trial_batches", 4),
                               "request_timeout_seconds": config["runtime"].get("session_timeout_seconds", config["model"].get("request_timeout_seconds", 300)),
                               "max_format_repairs": config["runtime"]["format_retries"],
                               "workspace": str(run_dir / "workspaces")}, PROJECT_ROOT)
    semaphore = asyncio.Semaphore(config["runtime"]["concurrency"])
    control = {"fatal_reason":None}
    completed = []
    for row in rows:
        result_path = run_dir / "results" / f"{row['sample_id']}.json"
        if resume and result_path.exists():
            saved = json.loads(result_path.read_text(encoding="utf-8"))
            if saved.get("ranking_complete"):
                completed.append(saved)
                continue
        case_started = time.monotonic()
        peaks = peaks_from_input(row)
        jobs, ids = {}, []
        for i, smiles in enumerate(row["generated"]):
            identity = candidate_key(smiles)
            cid = f"c{i+1:02d}"
            ids.append((cid, smiles, identity))
            if identity not in jobs:
                jobs[identity] = (cid, smiles)
        async def execute(cid, smiles):
            async with semaphore:
                return await process_candidate(row, cid, smiles, peaks, config, client, budget, checkpoints, mode, control)
        pairs = list(jobs.items())
        if mode == "agent" and config["runtime"].get("review_strategy", "legacy") == "case_batch":
            values = await process_case(row, [job for _, job in pairs], peaks, config, client, budget, checkpoints, control)
        else:
            values = await asyncio.gather(*(execute(cid, smiles) for _, (cid, smiles) in pairs))
        cache = {key: value for (key, _), value in zip(pairs, values)}
        candidates = []
        for cid, smiles, identity in ids:
            result = copy.deepcopy(cache[identity])
            result["computed_candidate_id"] = result["candidate_id"]
            result.update(candidate_id=cid, original_smiles=smiles)
            candidates.append(result)
        rank = rank_candidates(candidates)
        incomplete = any(c["stop_reason"] in RESOURCE_STOPS or not c.get("evidence", {}).get("complete", True) for c in candidates)
        incomplete = incomplete or not rank.get("ranking_complete", True)
        result = {**rank, "sample_id": row["sample_id"], "input_hash": digest(row), "mode": mode, "peaks": peaks,
                  "resource_usage": sample_resource_usage(budget, row["sample_id"], config["runtime"]),
                  "ranking_complete": not incomplete, "ranking_status": "provisional/incomplete" if incomplete else "complete"}
        result["resource_usage"]["workflow_wall_seconds"] = time.monotonic() - case_started
        result["resource_usage"]["wall_time_scope"] = "current invocation: initialization through ranking; excludes report rendering"
        export_sample(run_dir, result)
        completed.append(result)
        print(json.dumps({"sample_id": row["sample_id"], "completed": len(completed), "total": len(rows), "status": result["ranking_status"], "valid_outputs": sum(bool(c.get("final_smiles")) for c in candidates), f"spent_{budget.currency.lower()}": round(budget.spent, 6)}, ensure_ascii=False), flush=True)
        if any(c["stop_reason"] in {"configuration_error", "budget_exhausted", "offpeak_wait", "reservation_exceeded", "service_error", "usage_error", "authentication_error", "billing_error"} for c in candidates):
            break
    budget.save()
    summary = {"requested_samples": len(rows), "processed_samples": len(completed), "complete_samples": sum(r["ranking_complete"] for r in completed),
               f"spent_{budget.currency.lower()}": budget.spent, "currency": budget.currency, "mode": mode,
               "review_strategy": config["runtime"].get("review_strategy", "legacy")}
    write_json(run_dir / "summary.json", summary)
    return summary
