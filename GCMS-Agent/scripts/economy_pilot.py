"""Prepare a label-free stratified pilot or summarize actual inference costs.

No model calls. Raw test.json is deliberately rejected; use isolated inference/all.json.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import random
import re
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gcms_agent.config import digest, write_json
from gcms_agent.data.loader import load_inputs
from gcms_agent.data.deduplication import candidate_key
from gcms_agent.workflow.budget import DEFAULT_CNY, token_cost


def profile(row):
    from rdkit import Chem
    unique = {candidate_key(s): s for s in row["generated"]}
    repair, heavy = 0, []
    for smiles in unique.values():
        mol = Chem.MolFromSmiles(smiles)
        if mol is None or len(Chem.GetMolFrags(mol)) != 1 or Chem.GetFormalCharge(mol) != 0 or any(a.GetNumRadicalElectrons() for a in mol.GetAtoms()):
            repair += 1
        partial = mol if mol is not None else Chem.MolFromSmiles(smiles, sanitize=False)
        if partial is not None:
            heavy.append(partial.GetNumHeavyAtoms())
    n, peaks, atoms = len(unique), len(row["input_ids"]), max(heavy, default=0)
    complexity = "small" if n <= 4 and peaks <= 40 and atoms <= 7 else "medium" if n <= 7 and peaks <= 70 else "large"
    validity = "all_repair" if repair == n else "some_repair" if repair else "valid"
    return {"sample_id": row["sample_id"], "unique_candidates": n, "peak_count": peaks,
            "max_heavy_atoms": atoms, "repair_candidates": repair, "stratum": f"{complexity}_{validity}"}


def prepare_pilot(source, output, size=20, seed=20261103):
    from rdkit import RDLogger
    RDLogger.DisableLog("rdApp.error")
    rows = load_inputs(source)
    if not 1 <= size <= len(rows):
        raise ValueError("Pilot size must be positive and no larger than population")
    profiles = [profile(row) for row in rows]
    groups = defaultdict(list)
    by_id = {row["sample_id"]: row for row in rows}
    for item in profiles:
        groups[item["stratum"]].append(item)
    if size < len(groups):
        raise ValueError(f"At least {len(groups)} samples are needed to cover all strata")
    allocation = {key: 1 for key in groups}
    while sum(allocation.values()) < size:
        key = max((k for k in groups if allocation[k] < len(groups[k])),
                  key=lambda k: (size * len(groups[k]) / len(rows) - allocation[k], k))
        allocation[key] += 1
    randomizer = random.Random(seed)
    selected = []
    for key in sorted(groups):
        chosen = randomizer.sample(sorted(groups[key], key=lambda p: p["sample_id"]), allocation[key])
        selected.extend(chosen)
    selected.sort(key=lambda p: p["sample_id"])
    output = Path(output)
    manifest = {"source": str(Path(source).resolve()), "source_hash": digest(rows), "population": len(rows),
        "size": size, "seed": seed, "selection_uses_labels": False,
        "selection": "proportional strata with at least one per stratum; random seeded sampling within strata",
        "strata": {key: {"population": len(groups[key]), "selected": allocation[key], "weight": len(groups[key])/len(rows)} for key in sorted(groups)},
        "profiles": selected, "sample_ids": [p["sample_id"] for p in selected]}
    write_json(output, [by_id[item["sample_id"]] for item in selected])
    write_json(output.with_suffix(".manifest.json"), manifest)
    return manifest


def report_run(run_dir, population=1490, selection_manifest=None):
    if type(population) is not int or population <= 0:
        raise ValueError("Projection population must be a positive integer")
    run_dir = Path(run_dir)
    ledger = json.loads((run_dir / "logs/budget.json").read_text(encoding="utf-8-sig"))
    results = [json.loads(p.read_text(encoding="utf-8-sig")) for p in sorted((run_dir / "results").glob("*.json"))]
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig")) if manifest_path.exists() else {}
    requested = manifest.get("sample_ids", [])
    complete = [r for r in results if r.get("ranking_complete")]
    usages = {f: 0 for f in ("input_tokens", "cache_read_input_tokens", "output_tokens")}
    by_case = defaultdict(lambda: {f: 0 for f in usages})
    unknown = len(ledger.get("pending_reservations", {}))
    currency = ledger.get("currency", "USD")
    unknown_reserved = sum(ledger.get("pending_reservations", {}).values())
    for request in ledger.get("requests", []):
        usage = request.get("usage", {})
        if not usage.get("known", False):
            unknown += 1
            unknown_reserved += request.get(f"cost_{currency.lower()}", request.get("reservation", 0))
            continue
        for field in usages:
            usages[field] += usage.get(field, 0)
        match = re.search(r"s\d{5}_[a-f0-9]{12}", request["request_id"])
        sample_id = request.get("sample_id") or (match.group() if match else None)
        if sample_id:
            for field in usages:
                by_case[sample_id][field] += usage.get(field, 0)
    costs = {tariff: token_cost(usages, prices) for tariff, prices in DEFAULT_CNY.items()}
    complete_ids = {r.get("sample_id") for r in complete}
    known_case_ids = {sample_id for sample_id, values in by_case.items() if sum(values.values()) > 0}
    mode, backend = manifest.get("mode", "unknown"), manifest.get("backend", "unrecorded")
    projection_blockers = []
    if mode != "agent" or backend != "claude_agent_sdk":
        projection_blockers.append("not_a_recorded_live_claude_agent_sdk_run")
    if not requested or complete_ids != set(requested) or len(complete) != len(requested):
        projection_blockers.append("incomplete_or_mismatched_results")
    if not set(requested).issubset(known_case_ids):
        projection_blockers.append("missing_positive_known_usage_for_one_or_more_cases")
    if unknown:
        projection_blockers.append("unknown_or_inflight_usage")
    report = {"run_dir": str(run_dir.resolve()), "currency": ledger.get("currency", "USD"),
        "mode": mode, "backend": backend,
        "accounted_spend": ledger.get("spent_cny", ledger.get("spent_usd", 0)), "usage": usages,
        "sessions": len(ledger.get("requests", [])), "unknown_usage_sessions": unknown,
        "unknown_usage_reserved_cost": unknown_reserved, "unknown_usage_reserved_currency": currency,
        "requested_cases": len(requested), "result_cases": len(results), "complete_cases": len(complete),
        "known_usage_peak_cny": costs["peak"], "known_usage_offpeak_cny": costs["offpeak"],
        "projection_is_not_an_invoice_or_accuracy_guarantee": True,
        "can_project": not projection_blockers, "projection_blockers": projection_blockers,
        "projection_population": population, "projection": None}
    case_costs = [token_cost(value, DEFAULT_CNY["offpeak"]) for value in by_case.values()]
    if case_costs and unknown == 0:
        ordered = sorted(case_costs)
        report["case_offpeak_cny"] = {"mean": statistics.mean(ordered), "median": statistics.median(ordered),
            "p90": ordered[max(0, (9 * len(ordered) + 9)//10 - 1)], "max": max(ordered)}
    report["cases_exceeding_25000_output_target"] = [sample_id for sample_id, u in by_case.items() if u["output_tokens"] > 25000]
    if report["can_project"]:
        report["projection"] = {f"{tariff}_cny": amount / len(complete) * population for tariff, amount in costs.items()}
        report["projection_method"] = "unweighted sample mean; check representativeness"
        if selection_manifest:
            selection = json.loads(Path(selection_manifest).read_text(encoding="utf-8-sig"))
            expected = set(selection["sample_ids"])
            if expected != set(requested) or not expected.issubset(by_case):
                raise ValueError("Selection and run cases must match and every case must have usage")
            strata = defaultdict(list)
            for item in selection["profiles"]:
                strata[item["stratum"]].append(by_case[item["sample_id"]])
            report["projection"] = {f"{tariff}_cny": population * sum(
                selection["strata"][key]["weight"] * statistics.mean(token_cost(u, price) for u in members)
                for key, members in strata.items()) for tariff, price in DEFAULT_CNY.items()}
            report["projection_method"] = "population-weighted stratum means; small pilot uncertainty remains"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("source")
    prepare.add_argument("--output", required=True)
    prepare.add_argument("--size", type=int, default=20)
    prepare.add_argument("--seed", type=int, default=20261103)
    report = sub.add_parser("report")
    report.add_argument("run_dirs", nargs="+")
    report.add_argument("--population", type=int, default=1490)
    report.add_argument("--selection-manifest")
    report.add_argument("--output")
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare_pilot(args.source, args.output, args.size, args.seed)
    else:
        result = {"runs": [report_run(path, args.population, args.selection_manifest) for path in args.run_dirs]}
        result["known_usage_total_peak_cny"] = sum(r["known_usage_peak_cny"] for r in result["runs"])
        result["known_usage_total_offpeak_cny"] = sum(r["known_usage_offpeak_cny"] for r in result["runs"])
        result["unknown_usage_reserved_by_currency"] = {
            currency: sum(r["unknown_usage_reserved_cost"] for r in result["runs"] if r["currency"] == currency)
            for currency in sorted({r["currency"] for r in result["runs"]})}
        if args.output:
            write_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
