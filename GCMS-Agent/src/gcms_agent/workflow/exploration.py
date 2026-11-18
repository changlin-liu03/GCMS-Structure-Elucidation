"""Case-local structure facts, never reusable atom-mapped reaction paths."""
from copy import deepcopy
from gcms_agent.chemistry.smiles_io import mol_from_graph


def record_exploration(archive, candidate_id, validation, score, *, trial_id=None, selected=False):
    smiles = validation.get("canonical_smiles")
    if not validation.get("valid") or not smiles or score is None:
        return
    item = archive.setdefault(smiles, {
        "canonical_smiles": smiles, "score": score["score"],
        "supported_peaks": sorted(score.get("supported_peaks", [])),
        "ring_count": validation.get("ring_count"), "ring_sizes": [],
        "candidate_ids": [], "trial_ids": [], "selected_by_review": False})
    if validation.get("graph") and not item["ring_sizes"]:
        mol = mol_from_graph(validation["graph"])
        item["ring_sizes"] = sorted(len(r) for r in mol.GetRingInfo().AtomRings())
    if candidate_id not in item["candidate_ids"]:
        item["candidate_ids"].append(candidate_id)
        item["candidate_ids"].sort()
    if trial_id and trial_id not in item["trial_ids"]:
        item["trial_ids"].append(trial_id)
    item["selected_by_review"] |= selected is True


def record_audit(archive, audit):
    for trial in audit.get("trials", []):
        result = trial.get("result") or {}
        if (result.get("evidence") or {}).get("complete") is not True:
            continue
        record_exploration(archive, trial["candidate_id"], result.get("validation") or {},
                           result.get("score"), trial_id=trial.get("trial_id"),
                           selected=trial.get("selected", False))


def exploration_summary(archive, limit=40):
    # Include high support and recent hypotheses when the compact view is full.
    values = list(archive.values())
    ranked = sorted(values, key=lambda r: (-r["score"], r["canonical_smiles"]))
    chosen = ranked[:limit // 2]
    for item in reversed(values):
        if len(chosen) >= limit:
            break
        if item not in chosen:
            chosen.append(item)
    return {"total_structures": len(values), "omitted_structures": len(values) - len(chosen),
            "scope": "Completed prior waves of this case; descriptive facts only, no transferable trial IDs or atom mappings",
            "structures": [{**{k: deepcopy(v) for k, v in item.items() if k != "trial_ids"},
                            "trial_count": len(item["trial_ids"])} for item in chosen]}
