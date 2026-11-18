"""Deduplicate scientific identities while retaining every input lineage."""
from copy import deepcopy
import math


def _score(row):
    score = row.get("score", {})
    if isinstance(score, dict):
        return float(score.get("score", 0)), score
    return float(score or 0), {"score": float(score or 0), "confidence_score": 100 * float(score or 0)}


def evidence_profile(row):
    """Describe tool evidence without turning coverage into an identity claim.

    Categories can overlap at one observed peak and are never added together.
    The majority flag describes the central intensity estimate, not a new
    acceptance threshold or a probability of chemical correctness.
    """
    point, score = _score(row)
    evidence = row.get("evidence") or {}
    verified = [e for e in evidence.get("evidence", [])
                if e.get("verified") is True and e.get("used_for_scoring") is not False]
    scored_peaks_known = "supported_peaks" in score
    supported = set(score.get("supported_peaks", evidence.get("supported_peaks",
                    [e["peak_mz"] for e in verified if "peak_mz" in e])))
    paths = [e for e in verified if e.get("peak_mz") in supported]
    molecular = {e["peak_mz"] for e in paths if e.get("rule_id") == "molecular_ion"}
    fragments = {e["peak_mz"] for e in paths if e.get("rule_id") != "molecular_ion"}
    cleavages = {e["peak_mz"] for e in paths if e.get("broken_bonds")}
    weights = {p["mz"]: p["weight"] for p in score.get("peak_contributions", [])}
    unassigned = sorted((p for p in score.get("peak_contributions", []) if not p["supported"]),
                        key=lambda p: (-p["weight"], p["mz"]))[:5]
    molecular_only = bool(paths) and bool(molecular) and not fragments
    evidence_status = ("evidence_incomplete" if evidence.get("complete") is False else
                       "unscored" if row.get("score") is None else
                       "no_supported_peaks" if not supported else
                       "molecular_ion_only" if molecular_only else
                       "majority_intensity_unassigned" if point < .5 else "rule_support_available")
    return {
        "evidence_status": evidence_status,
        "strongest_unassigned_peaks": [{"mz": p["mz"], "weight": p["weight"]} for p in unassigned],
        "strongest_unassigned_intensity_fraction": sum(p["weight"] for p in unassigned),
        "rule_supported_intensity_fraction": point,
        "unassigned_intensity_fraction": 1 - point,
        "majority_intensity_unassigned_at_point_estimate": point < .5,
        "verified_path_count": len(paths),
        "supported_peaks": sorted(supported),
        "molecular_ion_peaks": sorted(molecular),
        "fragment_peaks": sorted(fragments),
        "bond_cleavage_peaks": sorted(cleavages),
        "molecular_ion_only": molecular_only,
        "fragment_intensity_coverage": sum(weights[p] for p in fragments) if weights else None,
        "bond_cleavage_intensity_coverage": sum(weights[p] for p in cleavages) if weights else None,
        "evidence_complete": evidence.get("complete") is True,
        "scored_peak_set_known": scored_peaks_known,
        "all_scored_peaks_have_verified_paths": scored_peaks_known and bool(supported) and supported <= {e["peak_mz"] for e in paths},
        "categories_may_overlap": True,
        "identity_confirmed": False,
        "isotope_ratio_verified": False,
        "fragment_abundance_predicted": False,
    }


def rank_candidates(results):
    groups, invalid, unranked = {}, [], []
    for result in results:
        row = deepcopy(result)
        smiles = row.get("final_smiles") or row.get("canonical_smiles")
        if not smiles or not row.get("validation", {}).get("valid", False):
            row["rank"] = None
            invalid.append(row)
            continue
        if row.get("score") is None or row.get("evidence", {}).get("complete") is False:
            row.update(rank=None, ranking_excluded_reason="evidence_incomplete")
            unranked.append(row)
            continue
        point, score = _score(row)
        if not math.isfinite(point) or not 0 <= point <= 1:
            raise ValueError("Ranking requires finite support scores in [0,1]")
        if smiles in groups:
            existing = groups[smiles]["score"]
            if any(abs(float(existing.get(k, existing["score"])) - float(score.get(k, point))) > 1e-12
                   for k in ("score", "score_lower", "score_upper")):
                raise ValueError("Identical final structures have inconsistent scores")
            groups[smiles]["lineages"].append(row)
        else:
            groups[smiles] = {"final_smiles": smiles, "score": {**score, "score": point}, "lineages": [row]}
    unique = sorted(groups.values(), key=lambda g: (-g["score"]["score"], g["final_smiles"]))
    previous, rank = None, 0
    for i, group in enumerate(unique):
        value = group["score"]["score"]
        if previous is None or abs(value - previous) > 1e-12:
            rank = i + 1
        group["rank"] = rank
        previous = value
    # Preserve evidence ranks. Only the highest-score tie uses source-slot votes;
    # votes never change coverage and are not independent spectral evidence.
    leaders = [g for g in unique if g["rank"] == 1]
    leaders.sort(key=lambda g: (-len(g["lineages"]), g["final_smiles"]))
    unique = leaders + [g for g in unique if g["rank"] != 1]
    for group in unique:
        group["tied"] = sum(g["rank"] == group["rank"] for g in unique) > 1
        group["source_candidate_ids"] = sorted(r.get("candidate_id", "") for r in group["lineages"])
        group["source_candidate_count"] = len(group["lineages"])
        group["independent_candidate_count"] = len({r.get("computed_candidate_id", r.get("candidate_id")) for r in group["lineages"]})
        group["evidence_profile"] = evidence_profile(group["lineages"][0])
        for row in group["lineages"]:
            row.update(rank=group["rank"], tied=group["tied"])
    flattened = [r for g in unique for r in g["lineages"]] + unranked + invalid
    public = [{k: v for k, v in g.items() if k != "lineages"} for g in unique]
    top1 = public[0] if public and public[0]["score"]["score"] > 0 else None
    status = "ranked" if top1 else "no_supported_peaks" if public else "no_valid_candidates"
    if unranked:
        status = "evidence_incomplete"
    top_candidates = [g for g in public if g["rank"] == 1] if top1 else []
    diagnostics = {"top_score_candidate_count": len(top_candidates),
                   "display_top1_is_unique_score_leader": bool(top1) and len(top_candidates) == 1,
                   "comparison_scope": "Returned scored unique structures only",
                   "identity_confirmed": False,
                   "tie_display_policy": "Highest-score ties: source candidate count descending, then canonical SMILES ascending; counts are source slots, not independent evidence",
                   "ranking_policy": "top_score_source_count_v1",
                   "intensity_basis": "raw_linear", "measurement_uncertainty_modeled": False,
                   "same_supported_peak_set_competitors": []}
    if top1:
        top1["identity_confirmed"] = False
        count_leaders = [g for g in top_candidates if g["source_candidate_count"] == top1["source_candidate_count"]]
        top1["selection_basis"] = ("highest_rule_support_score" if len(top_candidates) == 1
            else "most_source_candidates_among_top_score" if len(count_leaders) == 1
            else "canonical_display_representative_of_count_tie")
        diagnostics["top_source_count_candidate_count"] = len(count_leaders)
        top_peaks = set(top1["score"].get("supported_peaks", []))
        diagnostics["same_supported_peak_set_competitors"] = [g["final_smiles"] for g in public[1:]
            if "supported_peaks" in top1["score"] and "supported_peaks" in g["score"]
            and set(g["score"]["supported_peaks"]) == top_peaks]
        if len(public) > 1:
            diagnostics["point_score_gap_to_next"] = top1["score"]["score"] - public[1]["score"]["score"]
    return {"ranked_candidates": flattened, "unique_ranking": public, "unranked_candidates": unranked,
            "top1": top1, "top1_candidates": top_candidates, "ranking_diagnostics": diagnostics,
            "status": status, "ranking_complete": not unranked}

