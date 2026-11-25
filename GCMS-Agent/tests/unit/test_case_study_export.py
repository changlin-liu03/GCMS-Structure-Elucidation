"""Publication guards use synthetic fixtures; bundle checks use only public cases."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("case_export", PROJECT / "scripts/export_case_studies.py")
export = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(export)


def test_public_scan_allows_citations_but_rejects_machine_paths():
    export.check_public({"reference": "https://doi.org/10.0000/example"})
    with pytest.raises(ValueError, match="Local path"):
        export.check_public({"reference": "E:/private/source.json"})


@pytest.mark.parametrize("value", [
    {"api_key": "placeholder"}, {"detail": "sk-abcdefghijklmnopqrstuv"},
    {"sample_id": "s00001_0123456789ab"},
])
def test_public_scan_rejects_credentials_and_unapproved_samples(value):
    with pytest.raises(ValueError):
        export.check_public(value)


def test_frozen_inventory_rejects_modified_source(tmp_path):
    stage = tmp_path / "agent"
    stage.mkdir()
    result = stage / "result.json"
    result.write_text('{"ok":true}', encoding="utf-8")
    freeze = {"files": {"result.json": hashlib.sha256(result.read_bytes()).hexdigest()}}
    (stage / "prediction_freeze.json").write_text(json.dumps(freeze), encoding="utf-8")
    assert export.Source(tmp_path).verify_freeze("agent")["all_matched_at_export"]
    result.write_text('{"ok":false}', encoding="utf-8")
    with pytest.raises(ValueError, match="Frozen file mismatch"):
        export.Source(tmp_path).verify_freeze("agent")


def test_source_refuses_escape_and_workspace(tmp_path):
    source = export.Source(tmp_path)
    for name in ("../other-case/input.json", "agent/workspaces/config.json", "agent/example.key"):
        with pytest.raises(ValueError):
            source.file(name)


def test_graph_interning_preserves_atom_identity():
    pool = export.Pool()
    first = {"atoms": [{"id": "Mol_C_1", "element": "C"}], "bonds": []}
    other = {"atoms": [{"id": "Mol_C_2", "element": "C"}], "bonds": []}
    assert pool.ref("graphs", first) == pool.ref("graphs", {"bonds": [], "atoms": first["atoms"]})
    assert pool.ref("graphs", first) != pool.ref("graphs", other)


def test_unknown_usage_is_not_reported_as_zero_cost():
    summary = export.accounting({"currency": "CNY", "spent_cny": 0.6,
        "requests": [{"usage": {"known": True}, "cost_cny": 0.1},
                     {"usage": {"known": False}, "cost_cny": 0.5}],
        "pending_reservations": {"pending": 0.2}})
    assert summary["known_cost_cny"] == 0.1
    assert summary["estimated_cost_cny"] == 0.5
    assert summary["unknown_requests"] == 1
    assert summary["pending_reservation_cny"] == 0.2


@pytest.mark.parametrize("case", export.CASES, ids=lambda c: c["id"])
def test_public_bundle_integrity_and_full_trial_inventory(case):
    folder = PROJECT / "case_studies" / case["id"]
    assert {p.name for p in folder.iterdir()} == set(export.FILES)
    provenance = json.loads((folder / "provenance.json").read_text(encoding="utf-8"))
    assert set(provenance["exported_files_sha256"]) == set(export.FILES) - {"provenance.json"}
    for name, expected in provenance["exported_files_sha256"].items():
        assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == expected
    trajectory = json.loads((folder / "trajectory.json").read_text(encoding="utf-8"))
    result = json.loads((folder / "result.json").read_text(encoding="utf-8"))
    main = result["stages"]["agent"]
    sessions = trajectory["stages"]["agent"]["sessions"]
    assert len(sessions) == 8
    assert sum(len(s["trials"]) for s in sessions) == case["trials"]
    assert len(main["source_slots"]) == 10
    assert main["ranking_complete"] is False
    assert main["evaluation"]["strict_end_to_end_requested_set"]["acc_at_1"] == 0
    assert main["evaluation"]["strict_end_to_end_requested_set"]["acc_at_10"] == 0
    pool = trajectory["objects"]
    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in ("graph_ref", "score_ref") and child is not None:
                    assert child in pool["graphs" if key == "graph_ref" else "scores"]
                if key == "path_refs":
                    assert all(ref in pool["paths"] for ref in child)
                walk(child)
        elif isinstance(value, list):
            for child in value: walk(child)
    walk(trajectory)
    for p in folder.glob("*.json"):
        export.check_public(json.loads(p.read_text(encoding="utf-8")))
    inputs = json.loads((folder / "input.json").read_text(encoding="utf-8"))
    assert "true_smiles" not in inputs[0]
    assert "position_ids" not in inputs[0]
    assert inputs[0]["conditions"]["intensity_encoding"] == "raw_linear"
    chinese = (folder / "README.zh-CN.md").read_text(encoding="utf-8")
    english = (folder / "README.md").read_text(encoding="utf-8")
    assert "[English](README.md)" in chinese
    assert "[简体中文](README.zh-CN.md)" in english
    assert "provisional/incomplete" in english
    assert "original language" in english
    assert "../README.md" in english
    # Translation must not omit trials or alter any data row in the tables.
    def data_rows(document):
        rows = []
        for block in document.split("\n\n"):
            lines = block.splitlines()
            if len(lines) >= 2 and lines[0].startswith("| ") and lines[1].startswith("|---"):
                rows.extend(line.replace("未评分", "Not scored").replace("| 是 |", "| Yes |").replace("| 否 |", "| No |") for line in lines[2:])
        return rows
    assert data_rows(chinese) == data_rows(english)
    for session in sessions:
        for trial in session["trials"]:
            assert f"`{trial['trial_id']}`" in english
    for history in trajectory["stages"]["agent"]["candidates"][case["focus"]]["rounds"]:
        if history.get("summary"):
            assert export.model_prose(history["summary"]) in english
        for outcome in history["outcomes"]:
            if outcome["proposal"].get("reason"):
                assert export.model_prose(outcome["proposal"]["reason"]) in english


def test_model_smiles_in_prose_are_not_markdown_links():
    import html
    original = "CCCCC[Si](C)(C)C <unknown>"
    rendered = export.model_prose(original)
    assert "][" not in rendered and "](" not in rendered
    assert html.unescape(rendered) == original
