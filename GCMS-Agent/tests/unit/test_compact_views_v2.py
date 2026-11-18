"""Scientific values survive context factoring; no paid inference involved."""
from copy import deepcopy

import pytest

from gcms_agent.chemistry.smiles_io import parse_smiles
from gcms_agent.data.intensity import raw_peaks
from gcms_agent.harness.compact import compact_batch, compact_candidate, compact_trial, compact_score
from gcms_agent.harness.prompts import batch_inference_payload
from gcms_agent.harness.skill_loader import SkillLibrary
from gcms_agent.config import PROJECT_ROOT
from gcms_agent.tools.batch_server import BatchReviewTools
from gcms_agent.workflow.candidate import assess


def payload():
    peaks = raw_peaks([15, 31, 44, 46], [0, 20, 29, 15])
    graph = parse_smiles("CCO")["graph"]
    validation, evidence, score = assess(graph, peaks)
    return {"sample_id": "synthetic-compact", "peaks": peaks,
            "candidates": [{"candidate_id": "c01", "graph": graph, "graph_version": 3,
                "validation": validation, "evidence": evidence, "score": score,
                "round": 2, "previous_feedback": []}], "rules_version": "synthetic"}


def resolve(view, row):
    if "score_ref" not in row:
        return row["score"]
    if row["score_ref"] == "current":
        return compact_score(view["score"], include_unassigned=False)
    return view["score_references"][row["score_ref"]]


def test_weights_preserve_all_peaks_and_exact_full_spectrum_normalization():
    item = payload()
    before = deepcopy(item)
    view = compact_batch(item)
    assert view["spectrum"] == [[p["mz"], p["intensity"]] for p in item["peaks"]]
    denominator = sum(p["intensity"] for p in item["peaks"])
    assert view["spectrum_weights"] == pytest.approx([p["intensity"] / denominator for p in item["peaks"]])
    assert sum(view["spectrum_weights"]) == pytest.approx(1)
    assert 15 not in [row[0] for row in view["spectrum"]]  # zero signal is not observed
    assert item == before
    tool = BatchReviewTools(item, SkillLibrary(PROJECT_ROOT))
    assert view["spectrum_weights"] == [p["weight"] for p in tool.get_spectrum({})["peaks"]]


def test_linear_intensity_needs_no_decoding():
    item = payload()
    item["peaks"] = raw_peaks([31], [20])
    assert compact_batch(item)["spectrum_weights"] == [1.0]
    assert compact_batch(item)["spectrum"] == [[31, 20]]


def test_both_model_modes_and_lookup_preserve_original_mz_intensity_pairs():
    from gcms_agent.harness.prompts import batch_user_prompt, user_prompt, system_prompt, SPECTRUM_READING_GUIDANCE
    import json
    item = payload()
    item["peaks"] = raw_peaks([15, 39, 54], [51.25, 761, 999])
    view = json.loads(batch_user_prompt(item).split("\n", 1)[1])
    assert view["spectrum"] == [[15, 51.25], [39, 761], [54, 999]]
    assert view["intensity_encoding"]["normalization"] == "none"
    single = json.loads(user_prompt({**item["candidates"][0], "peaks": item["peaks"]}).split("\n", 1)[1])
    assert [(p["mz"], p["intensity"]) for p in single["peaks"]] == [(15, 51.25), (39, 761), (54, 999)]
    library = SkillLibrary(PROJECT_ROOT)
    for mode in ("legacy", "case_batch"):
        prompt = system_prompt(library, mode)
        assert SPECTRUM_READING_GUIDANCE in prompt
        assert "intensity=I/max(I)" not in prompt
        assert "基峰为1" not in prompt
    tool = BatchReviewTools(item, library)
    peak = tool.get_spectrum({"mz": [54]})["peaks"][0]
    assert peak["intensity"] == peak["raw_intensity"] == 999
    assert peak["weight"] == pytest.approx(999 / (51.25 + 761 + 999))


def test_raw_scale_weights_do_not_overflow_and_do_not_replace_spectrum():
    item = payload()
    item["peaks"] = raw_peaks([31, 44], [1e308, 1e308])
    view = compact_batch(item)
    assert view["spectrum"] == [[31, 1e308], [44, 1e308]]
    assert view["spectrum_weights"] == [.5, .5]
    tool = BatchReviewTools(item, SkillLibrary(PROJECT_ROOT))
    assert [p["weight"] for p in tool.get_spectrum({})["peaks"]] == [.5, .5]


def test_missing_trial_evidence_is_not_presented_as_completed_analysis():
    view = compact_trial({"validation": {"valid": False}, "evidence": None, "score": None})
    assert view["evidence"]["complete"] is False
    completed_empty = compact_trial({"validation": {"valid": True},
                                     "evidence": {"complete": True, "evidence": []}, "score": None})
    assert completed_empty["evidence"]["complete"] is True


def test_factored_evidence_table_keeps_stable_query_ids_and_every_bond():
    item = payload()
    original = item["candidates"][0]["evidence"]
    view = compact_candidate(item["candidates"][0])["evidence"]
    paths = [dict(zip(view["path_columns"], row)) for row in view["paths"]]
    state = BatchReviewTools(item, SkillLibrary(PROJECT_ROOT))
    for index, (row, actual) in enumerate(zip(paths, original["evidence"]), 1):
        assert row["evidence_id"] == f"e{index}"
        for field in view["path_columns"][1:]:
            assert row[field] == actual.get(field)
        queried = state.get_evidence({"candidate_id": "c01", "evidence_ids": [row["evidence_id"]]})
        assert queried["evidence"][row["evidence_id"]] == actual
    assert len(paths) == len(original["evidence"])


def test_history_and_memory_reference_exact_scores_without_repeating_unassigned_peaks():
    item = payload()["candidates"][0]
    current = deepcopy(item["score"])
    different = deepcopy(current)
    different.update(score=0.12345678901234567, score_lower=0.1, score_upper=0.2, supported_peaks=[44])
    item["previous_feedback"] = [{"round": 1, "outcomes": [
        {"validation": {"canonical_smiles": "CCO"}, "score": current, "committed": True},
        {"validation": {"canonical_smiles": "CC=O"}, "score": different, "committed": False},
    ]}]
    item["trial_memory"] = [
        {"trial_id": "t-current", "base_version": 2, "score": current, "selected": True},
        {"trial_id": "t-pending", "base_version": 3, "score": different,
         "verdict": {"accepted": True}, "selected": False},
    ]
    before = deepcopy(item)
    view = compact_candidate(item)
    current_history, alternative_history = view["previous_feedback"][0]["outcomes"]
    current_memory, alternative_memory = sorted(view["trial_memory"], key=lambda row: row["trial_id"])
    assert current_history["score_ref"] == "current"
    assert current_memory["score_ref"] == "current"
    assert alternative_history["score_ref"] == alternative_memory["score_ref"]
    assert resolve(view, alternative_history) == compact_score(different, include_unassigned=False)
    assert resolve(view, alternative_memory)["score"] == different["score"]
    assert "unassigned_peaks" not in resolve(view, alternative_memory)
    assert view["score"]["unassigned_peaks"] == current["unassigned_peaks"]
    assert item == before


def test_pending_trials_cannot_be_lost_by_score_factoring_or_recent_window():
    item = payload()["candidates"][0]
    score = deepcopy(item["score"])
    pending = [{"trial_id": f"p{i}", "base_version": 3, "score": score,
                "verdict": {"accepted": True}, "selected": False} for i in range(30)]
    others = [{"trial_id": f"r{i}", "base_version": 2, "score": score,
               "verdict": {"accepted": False}, "selected": False} for i in range(35)]
    item["trial_memory"] = pending + others
    view = compact_candidate(item)
    ids = {row["trial_id"] for row in view["trial_memory"]}
    assert {row["trial_id"] for row in pending} <= ids
    assert len(ids) == 50
    assert all(resolve(view, row) == compact_score(score, include_unassigned=False) for row in view["trial_memory"])


def test_trial_unsupported_peaks_are_reconstructible_without_repetition():
    item = payload()["candidates"][0]
    trial = compact_trial({k: item[k] for k in ("validation", "score", "evidence")}, item["score"])
    assert "unassigned_peaks" not in trial["score"]
    assert trial["new_supported_peaks"] == []
    all_mz = {p["mz"] for p in payload()["peaks"]}
    assert sorted(all_mz - set(trial["score"]["supported_peaks"])) == item["score"]["unassigned_peaks"]


def test_null_score_remains_null_and_private_cache_never_enters_context():
    item = payload()
    item["candidates"][0]["score"] = None
    item["candidates"][0]["trial_memory"] = [{"trial_id": "bad", "base_version": 3,
        "score": None, "verdict": {"accepted": False}, "selected": False}]
    item["trial_cache"] = {"true_smiles": "private-not-prompt"}
    view = compact_batch(item)
    assert view["candidates"][0]["score"] is None
    assert view["candidates"][0]["trial_memory"][0]["score"] is None
    assert "private-not-prompt" not in str(view)
    item["candidates"][0]["labels"] = []
    with pytest.raises(ValueError, match="Forbidden"):
        compact_batch(item)


def test_session_budget_survives_whitelist_without_changing_scientific_facts():
    item = payload()
    original = compact_batch(item)
    limits = {"max_tool_turns": 8, "reserve_submit_turns": 2,
              "max_non_submission_tool_calls": 6}
    item["session_budget"] = {**limits, "private_transport_state": "must-not-enter-prompt"}
    before = deepcopy(item)
    view = compact_batch(batch_inference_payload(item))
    assert view.pop("session_budget") == limits
    assert view == original
    assert "must-not-enter-prompt" not in str(view)
    assert item == before


@pytest.mark.parametrize("value", [True, -1, 2.5, "6"])
def test_invalid_submission_budget_does_not_reach_model(value):
    item = payload()
    item["session_budget"] = {"max_non_submission_tool_calls": value}
    with pytest.raises(ValueError, match="nonnegative integers"):
        compact_batch(item)
