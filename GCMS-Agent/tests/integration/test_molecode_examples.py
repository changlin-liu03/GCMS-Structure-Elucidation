"""Run the documented teaching graphs and edits through the current tools."""
import importlib.util
import json
from pathlib import Path

from rdkit import rdBase


def test_documented_molecode_examples_match_verified_structures_and_edits():
    project = Path(__file__).resolve().parents[2]
    script = project / "scripts" / "validate_molecode_skill.py"
    spec = importlib.util.spec_from_file_location("molecode_teaching_validation", script)
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)

    # Some examples intentionally contain invalid graphs or rejected edits.
    # The verifier checks those outcomes without retaining expected RDKit logs.
    with rdBase.BlockLogs():
        report = validator.verify()

    assert report["cases"] > 0, "No teaching examples were checked"
    assert report["passed"] == report["cases"], json.dumps(report, ensure_ascii=False, indent=2)
