import math

RAW_ENCODING = "raw_linear"
LEGACY_INPUT_ERROR = "Raw intensity field required; legacy position_ids is unsupported. Re-run audit on the raw-intensity source into a new data directory."


def raw_peaks(masses, intensities):
    """Preserve input-scale linear measurements; zero signals are not observed peaks."""
    if not isinstance(masses, list) or not isinstance(intensities, list) or not masses or len(masses) != len(intensities):
        raise ValueError("Peak arrays must be nonempty lists of equal length")
    if any(type(m) is not int or m <= 0 for m in masses) or len(set(masses)) != len(masses):
        raise ValueError("Nominal masses must be unique positive integers")
    if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in intensities):
        raise ValueError("Raw intensities must be finite nonnegative numbers")
    maximum = max(intensities)
    if maximum <= 0:
        raise ValueError("Spectrum must contain a positive intensity")
    peaks = []
    for mz, value in zip(masses, intensities):
        if value == 0:
            continue
        if value / maximum == 0:
            raise ValueError("Intensity dynamic range exceeds floating-point precision")
        peaks.append({"mz": mz, "raw_intensity": value, "intensity": value,
                      "lower": value, "upper": value, "intensity_encoding": RAW_ENCODING})
    return sorted(peaks, key=lambda p: p["mz"])


def spectrum_weights(peaks):
    """Full-spectrum fractions; scaling here is only for numerical stability."""
    if not peaks:
        return []
    maximum = max(p["intensity"] for p in peaks)
    scaled = [p["intensity"] / maximum for p in peaks]
    total = math.fsum(scaled)
    return [value / total for value in scaled]


def peaks_from_input(row):
    if not isinstance(row, dict):
        raise ValueError("Spectrum input must be an object")
    if "position_ids" in row or "intensity" not in row:
        raise ValueError(LEGACY_INPUT_ERROR)
    conditions = row.get("conditions", {})
    if not isinstance(conditions, dict):
        raise ValueError("conditions must be an object")
    if conditions.get("intensity_encoding", RAW_ENCODING) != RAW_ENCODING:
        raise ValueError("Only raw_linear intensity is supported")
    return raw_peaks(row.get("input_ids"), row["intensity"])
