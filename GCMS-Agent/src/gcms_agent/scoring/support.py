import math


def calculate_support(peaks, supported_peaks):
    supported = set(supported_peaks)
    if not peaks:
        raise ValueError("Cannot score an empty spectrum")
    if len({p["mz"] for p in peaks}) != len(peaks):
        raise ValueError("Spectrum must have unique m/z bins")
    rows = []
    for p in peaks:
        if not isinstance(p.get("mz"), int) or isinstance(p["mz"], bool) or p["mz"] <= 0:
            raise ValueError("Unit-resolution m/z must be a positive integer")
        intensity = p.get("intensity")
        if type(intensity) not in (int, float) or not math.isfinite(intensity) or intensity <= 0:
            raise ValueError("Scored peaks require positive finite linear intensity")
        rows.append((p["mz"], intensity))
    scale = max(v for _, v in rows)
    rows = [(mz, v / scale) for mz, v in rows]
    denominator = math.fsum(v for _, v in rows)
    point = math.fsum(v for mz, v in rows if mz in supported) / denominator
    actual = sorted(p["mz"] for p in peaks if p["mz"] in supported)
    return {"score": point, "confidence_score": 100 * point,
            "intensity_basis": "raw_linear",
            "score_lower": point, "score_upper": point,
            "supported_peaks": actual, "unassigned_peaks": sorted(p["mz"] for p in peaks if p["mz"] not in supported),
            "score_semantics": "Uncalibrated rule-supported intensity coverage; not probability of correct identity",
            "peak_contributions": [{"mz": mz, "weight": v / denominator, "supported": mz in supported,
                                    "contribution": v / denominator if mz in supported else 0.0} for mz, v in rows]}

