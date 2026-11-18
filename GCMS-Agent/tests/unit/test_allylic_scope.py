import pytest

from gcms_agent.chemistry.smiles_io import parse_smiles
from gcms_agent.fragmentation.enumeration import analyze_spectrum
from gcms_agent.fragmentation.rule_engine import enumerate_paths, load_rule_policy
from gcms_agent.scoring.support import calculate_support


def graph_for(smiles):
    parsed = parse_smiles(smiles)
    assert parsed["valid"]
    return parsed["graph"]


@pytest.mark.parametrize("smiles,mass,formula", [
    ("CCC=C", 41, "C3H5"),
    ("CC(C)C=C", 55, "C4H7"),
    ("CC1C=CCCC1", 81, "C6H9"),
])
def test_saturated_allylic_sites_still_fragment(smiles, mass, formula):
    graph = graph_for(smiles)
    paths = [p for p in enumerate_paths(graph, load_rule_policy()) if p["rule_id"] == "allylic"]
    assert any(p["nominal_mass"] == mass and p["ion_formula"] == formula for p in paths)
    for p in paths:
        endpoints = p["broken_bonds"][0]
        retained_endpoints = [a for a in endpoints if a in p["atom_ids"]]
        assert len(retained_endpoints) == 1
        keep = retained_endpoints[0]
        assert all(b["order"] == 1 for b in graph["bonds"] if keep in (b["a"], b["b"]))
        assert p["verified"] and p["neutral_losses"][0]["radicals"] == 1


@pytest.mark.parametrize("smiles", [
    "C=C(C#N)C(=C)C#N", "C=C(C#N)C=CC#N", "C=C(C)C=C", "C=CC#CC",
])
def test_vinylic_and_sp_sites_do_not_receive_allylic_credit(smiles):
    assert not any(p["rule_id"] == "allylic"
                   for p in enumerate_paths(graph_for(smiles), load_rule_policy()))


def test_case_isomers_lose_extra_78_but_keep_nitrile_hcn_evidence():
    spectrum = [{"mz": m, "intensity": 29, "lower": 1/1.28, "upper": 1.,
                 "intensity": (1/1.28)**.5} for m in (77, 78, 104)]
    scores = []
    for smiles in ("N#CC=CC=CC#N", "C=C(C#N)C(=C)C#N", "C=C(C#N)C=CC#N"):
        evidence = analyze_spectrum(graph_for(smiles), spectrum)
        assert evidence["complete"]
        assert evidence["supported_peaks"] == [77, 104]
        assert {p["rule_id"] for p in evidence["evidence"]} == {"molecular_ion", "vinyl_nitrile_hcn_loss"}
        scores.append(calculate_support(spectrum, evidence["supported_peaks"])["score"])
    assert scores[0] == scores[1] == scores[2]


def test_ring_single_cut_is_still_not_a_fragmentation():
    assert not any(p["rule_id"] == "allylic"
                   for p in enumerate_paths(graph_for("C1=CCCCC1"), load_rule_policy()))
