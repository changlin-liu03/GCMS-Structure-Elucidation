"""Describe the limits of an explanation independently of model prose."""

from gcms_agent.scoring.ranking import evidence_profile


def explanation_quality(result):
    complete = result.get("ranking_complete", result.get("ranking_status") == "complete")
    top = result.get("top1")
    quality = {
        "workflow_complete": bool(complete), "provisional": not complete,
        "identity_confirmed": False, "mechanism_experimentally_confirmed": False,
        "model_narrative_verification": "not_verified",
        "host_verification_scope": "Rule applicability, chemical bookkeeping and observed nominal-mass matching only",
        "claims_not_validated": ["unique_molecular_identity", "fragment_abundance_or_dominant_pathway",
                                 "isotope_intensity_ratio", "chemical_names_or_conjugation_in_model_prose"],
        "limitations": [],
    }
    if not complete:
        quality["limitations"].append("workflow_incomplete")
    if top is None:
        quality["limitations"].append("no_supported_top1")
        return quality
    selected = next(c for c in result["ranked_candidates"] if c.get("final_smiles") == top["final_smiles"])
    profile = evidence_profile(selected)
    quality["evidence_profile"] = profile
    unique = result.get("unique_ranking", [])
    ties = result.get("top1_candidates", [g for g in unique if g.get("rank") == 1])
    quality["top_score_candidate_smiles"] = [g["final_smiles"] for g in ties]
    quality["display_top1_selection_basis"] = top.get("selection_basis", "legacy_display_order")
    if top.get("tied") or len(ties) > 1:
        quality["limitations"].append("multiple_structures_share_top_score")
    if profile["majority_intensity_unassigned_at_point_estimate"]:
        quality["limitations"].append("majority_intensity_unassigned_at_point_estimate")
    if profile["molecular_ion_only"]:
        quality["limitations"].append("molecular_ion_only")
    if not profile["bond_cleavage_peaks"]:
        quality["limitations"].append("no_rule_supported_bond_cleavage_peaks")
    if not profile["evidence_complete"] or not profile["all_scored_peaks_have_verified_paths"]:
        quality["limitations"].append("verified_path_coverage_incomplete")
    same = result.get("ranking_diagnostics", {}).get("same_supported_peak_set_competitors", [])
    quality["same_supported_peak_set_competitors"] = same
    if same:
        quality["limitations"].append("competing_structures_explain_same_peak_set")
    return quality


def model_text_block(value):
    """Keep raw model statements auditable, without rendering them as findings."""
    import re
    text = str(value)
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return ["模型文字记录（未验证，不属于工具证实的结论）：", "", fence + "text", text, fence, ""]
