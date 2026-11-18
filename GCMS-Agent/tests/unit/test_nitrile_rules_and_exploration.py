import asyncio
import json

import pytest

from gcms_agent.chemistry.smiles_io import parse_smiles
from gcms_agent.config import PROJECT_ROOT, load_config, write_json
from gcms_agent.fragmentation.conservation import atom_counts, check_conservation
from gcms_agent.fragmentation.enumeration import analyze_spectrum
from gcms_agent.fragmentation.rule_engine import enumerate_paths, load_rule_policy
from gcms_agent.scoring.acceptance import accept_edit
from gcms_agent.scoring.support import calculate_support
from gcms_agent.workflow.case import initialize_candidate, apply_review, finish
from gcms_agent.workflow.runner import run_batch
from gcms_agent.workflow.search_state import resume_exploration
from gcms_agent.harness.skill_loader import SkillLibrary
from gcms_agent.tools.batch_server import BatchReviewTools, CHECKS


def peaks(*values):
    return [{"mz": m, "intensity": 29, "lower": 1/1.28, "upper": 1.,
             "intensity": (1/1.28)**.5} for m in values]


def paths(smiles, rule):
    parsed = parse_smiles(smiles)
    assert parsed["valid"]
    result = analyze_spectrum(parsed["graph"], peaks(*range(1, 160)))
    assert result["complete"]
    return [p for p in result["evidence"] if p["rule_id"] == rule]


def test_vinyl_nitrile_hcn_atom_accounting_and_bond_change():
    graph = parse_smiles("C=CC#N")["graph"]
    path, = paths("C=CC#N", "vinyl_nitrile_hcn_loss")
    assert path["peak_mz"] == 26 and path["ion_formula"] == "C2H2"
    assert path["neutral_losses"][0]["formula"] == "CHN"
    assert path["radicals"] == 1 and path["neutral_losses"][0]["radicals"] == 0
    assert list(path["hydrogen_changes"].values()) == [-1]
    assert path["bond_changes"][0]["from"] == 2 and path["bond_changes"][0]["to"] == 3
    atoms = {a["id"]: a for a in graph["atoms"]}
    ion = atom_counts(atoms[i] for i in path["atom_ids"])
    ion[("H", 0)] -= 1
    neutral = atom_counts(atoms[i] for i in path["neutral_losses"][0]["atom_ids"])
    neutral[("H", 0)] += 1
    assert check_conservation(atom_counts(graph["atoms"]), ion, [neutral])
    assert not path["mechanism_confirmed"]
    assert path["atom_mapping_status"] == "hypothesis_not_experimentally_confirmed"
    assert path["reference"]


@pytest.mark.parametrize("smiles", ["CCC#N", "CC#N", "N#CC=C(F)F", "N#CC1=CCCCC1", "N#Cc1ccccc1", "C=C[N+]#[C-]"])
def test_vinyl_rule_excludes_missing_motif_or_beta_h(smiles):
    assert not paths(smiles, "vinyl_nitrile_hcn_loss")


def test_nitrile_rules_respect_isotopes_and_policy():
    isotope, = paths("C=C[13C]#N", "vinyl_nitrile_hcn_loss")
    assert isotope["peak_mz"] == 26
    assert "[13C]" in isotope["neutral_losses"][0]["formula"]
    graph = parse_smiles("C=CC#N")["graph"]
    policy = load_rule_policy()
    policy["enabled"] = ["molecular_ion"]
    assert len(enumerate_paths(graph, policy)) == 1
    policy["enabled"].append("vinyl_nitrile_hcn_loss")
    policy["max_paths"] = 1
    with pytest.raises(ValueError, match="enumeration_limit"):
        enumerate_paths(graph, policy)


def test_aryl_nitrile_cn_loss_excludes_aliphatic_and_tracks_radical():
    path, = paths("N#Cc1ccccc1", "aryl_nitrile_cn_loss")
    assert path["peak_mz"] == 77 and path["ion_formula"] == "C6H5"
    assert path["neutral_losses"][0]["formula"] == "CN"
    assert path["neutral_losses"][0]["radicals"] == 1 and path["radicals"] == 0
    assert path["hydrogen_changes"] == {}
    assert not paths("C=CC#N", "aryl_nitrile_cn_loss")
    assert not paths("CCC#N", "aryl_nitrile_cn_loss")


@pytest.mark.parametrize("support", [[], [29]])
def test_equal_and_lower_scores_need_no_new_peak(support):
    old = calculate_support(peaks(29, 99), [29])
    new = calculate_support(peaks(29, 99), support)
    verdict = accept_edit({"valid": True, "canonical_smiles": "CC"},
                          {"valid": True, "canonical_smiles": "C=C"}, old, new)
    assert verdict["accepted"] and verdict["reason"] == "valid_exploration"
    assert verdict["new_supported_peaks"] == []
    assert "threshold" not in verdict


def proposal(candidate, order):
    graph = candidate["graph"]
    operations = [{"op": "set_bond", "a": graph["atoms"][0]["id"],
                   "b": graph["atoms"][1]["id"], "order": order}]
    operations += [{"op": "set_atom", "id": a["id"], "hydrogens": 4-order} for a in graph["atoms"]]
    return {"base_version": candidate["graph_version"], "operations": operations,
            "reason": "Explore a different bond order", "peak_refs": [],
            "operation_explanations": ["Bond order and matching hydrogen state"] * len(operations)}


def test_finish_and_resume_preserve_best_and_exploration_versions():
    spectrum = peaks(29, 30, 99)
    state = initialize_candidate("c01", "CC", spectrum, {})
    review = {"decision": "edit", "proposals": [proposal(state, 2)], "summary": "Explore lower score"}
    apply_review(state, review, spectrum, load_config(), "test", {})
    assert state["validation"]["canonical_smiles"] == "C=C"
    assert state["best_state"]["validation"]["canonical_smiles"] == "CC"
    finish(state, "service_error")
    assert state["final_smiles"] == "CC" and state["result_status"] == "kept"
    state = json.loads(json.dumps(state))  # persisted checkpoint round trip
    resume_exploration(state)
    assert state["validation"]["canonical_smiles"] == "C=C" and state["graph_version"] == 1
    assert state["best_state"]["graph_version"] == 0
    state["finished"] = False
    apply_review(state, {"decision": "edit", "proposals": [proposal(state, 1)], "summary": "Duplicate"},
                 spectrum, load_config(), "test2", {})
    assert state["history"][-1]["outcomes"][0]["verdict"]["reason"] == "duplicate_structure"


def test_higher_scoring_exploration_promotes_best():
    spectrum = peaks(30, 99)
    state = initialize_candidate("c01", "C=C", spectrum, {})
    apply_review(state, {"decision": "edit", "proposals": [proposal(state, 1)], "summary": "New best"},
                 spectrum, load_config(), "test", {})
    finish(state, "max_rounds")
    assert state["final_smiles"] == "CC" and state["result_status"] == "corrected"
    assert state["best_state"]["graph_version"] == 1


def test_tools_allow_lower_trial_without_forcing_submission_and_reject_seen():
    spectrum = peaks(29, 30, 99)
    candidate = initialize_candidate("c01", "CC", spectrum, {})
    payload = {"sample_id": "test", "peaks": spectrum, "candidates": [candidate],
               "rules_version": "test", "max_trial_batches": 4}
    tools = BatchReviewTools(payload, SkillLibrary(PROJECT_ROOT))
    trial = {k: v for k, v in proposal(candidate, 2).items() if k in ("base_version", "operations")}
    trial["candidate_id"] = "c01"
    response = tools.analyze({"proposals": [trial]})
    verdict = response["results"][0]["verdict"]
    assert verdict["accepted"] and not verdict["improves_best"]
    # Valid exploration is optional; it must not become an unsubmitted
    # improvement that prevents a legitimate completed keep review.
    submission = tools.validate_submission({"reviews": [{"candidate_id": "c01", "decision": "keep",
        "proposals": [], "summary": "Explored alternative", "checks": list(CHECKS)}]})
    assert submission["reviews"][0]["stop_evidence"]["verified"]
    candidate["seen"].append("C=C")
    tools = BatchReviewTools(payload, SkillLibrary(PROJECT_ROOT))
    assert tools.analyze({"proposals": [trial]})["results"][0]["verdict"]["reason"] == "duplicate_structure"


@pytest.mark.parametrize("strategy", ["case_batch", "legacy"])
def test_workflow_continues_from_lower_scoring_structure_and_returns_best(tmp_path, strategy):
    class Reviewer:
        async def review(self, c):
            if c["round"] == 2:
                assert c["validation"]["canonical_smiles"] == "C=C"
            return {"decision": "edit" if c["round"] < 3 else "keep",
                    "proposals": [proposal(c, c["round"]+1)] if c["round"] < 3 else [],
                    "summary": "Synthetic search", "usage": {"known": True, "input_tokens": 0, "output_tokens": 0}}

        async def review_batch(self, payload):
            reviews = [{"candidate_id": c["candidate_id"], **await self.review(c)} for c in payload["candidates"]]
            return {"reviews": reviews, "usage": {"known": True, "input_tokens": 0, "output_tokens": 0}}

    config = load_config()
    config["runtime"].update(review_strategy=strategy, offpeak_only=False)
    source = tmp_path / "input.json"
    write_json(source, [{"sample_id": "test", "input_ids": [29, 30, 99],
                         "intensity": [29, 29, 29], "generated": ["CC"]*10}])
    asyncio.run(run_batch(source, tmp_path / "run", config, client=Reviewer()))
    result = json.loads((tmp_path / "run/results/test.json").read_text(encoding="utf-8"))
    c = result["ranked_candidates"][0]
    assert c["accepted_edits"] == 2 and c["final_smiles"] == "CC"
    assert c["result_status"] == "kept"
    assert c["exploration_state"]["validation"]["canonical_smiles"] == "C#C"
    assert c["exploration_state"]["graph_version"] == 2
    report = (tmp_path / "run/reports/test.md").read_text(encoding="utf-8")
    assert "最后探索结构" in report and "历史最高点分" in report
