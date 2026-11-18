import math


def accept_edit(old_validation, new_validation, old_score, new_score):
    if not new_validation.get("valid", False):
        return {"accepted": False, "reason": "chemical_validation_failed"}
    if not old_validation.get("valid", False):
        return {"accepted": True, "reason": "first_valid_repair"}
    if old_validation.get("canonical_smiles") == new_validation.get("canonical_smiles"):
        return {"accepted": False, "reason": "same_structure"}
    if not new_score:
        return {"accepted": False, "reason": "evidence_incomplete"}
    for score in (old_score, new_score):
        if score is None:
            continue
        low, point, high = score.get("score_lower"), score.get("score"), score.get("score_upper")
        if any(not isinstance(x, (int, float)) or not math.isfinite(x) for x in (low, point, high)) or not 0 <= low <= point <= high <= 1:
            return {"accepted": False, "reason": "invalid_score_bounds"}
    new_peaks = set(new_score["supported_peaks"]) - set((old_score or {}).get("supported_peaks", []))
    return {"accepted": True, "reason": "valid_exploration",
            "score_change": new_score["score"] - old_score["score"] if old_score else None,
            "new_supported_peaks": sorted(new_peaks)}

