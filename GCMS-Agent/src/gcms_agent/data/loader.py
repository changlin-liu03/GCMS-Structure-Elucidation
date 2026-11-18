import json
import re
from pathlib import Path
from gcms_agent.data.intensity import peaks_from_input, LEGACY_INPUT_ERROR

ALLOWED = {"sample_id", "input_ids", "intensity", "generated", "conditions"}

def validate_input(row):
    if not isinstance(row, dict):
        raise ValueError("Inference row must be an object")
    if "position_ids" in row:
        raise ValueError(LEGACY_INPUT_ERROR)
    if set(row) - ALLOWED:
        raise ValueError("Inference input contains forbidden/unknown fields")
    if not isinstance(row.get("sample_id"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", row["sample_id"]):
        raise ValueError("sample_id must be a safe ASCII identifier")
    conditions = row.get("conditions", {})
    if not isinstance(conditions, dict) or set(conditions) - {"ionization", "energy_ev", "energy_is_assumed", "resolution", "intensity_encoding"}:
        raise ValueError("Unknown experimental-condition field")
    if conditions.get("ionization", "EI") != "EI" or conditions.get("resolution", "nominal") != "nominal":
        raise ValueError("This version supports nominal-mass EI spectra only")
    if "energy_ev" in conditions and (type(conditions["energy_ev"]) not in (int, float) or not 0 < conditions["energy_ev"] < 1000):
        raise ValueError("Invalid EI energy")
    if "energy_is_assumed" in conditions and type(conditions["energy_is_assumed"]) is not bool:
        raise ValueError("energy_is_assumed must be boolean")
    generated = row.get("generated")
    if not isinstance(generated, list) or len(generated) != 10 or any(not isinstance(s, str) or not s.strip() for s in generated):
        raise ValueError("Exactly ten nonempty candidate SMILES are required")
    peaks_from_input(row)
    return row

def load_inputs(path):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, list):
        raise ValueError("Expected a JSON array")
    rows = [validate_input(row) for row in data]
    if len({row["sample_id"] for row in rows}) != len(rows):
        raise ValueError("Duplicate sample IDs")
    return rows
