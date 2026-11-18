from copy import deepcopy

import pytest
from rdkit import Chem

from gcms_agent.chemistry.smiles_io import graph_from_mol, parse_smiles
from gcms_agent.chemistry.validation import validate_graph
from gcms_agent.fragmentation import enumeration
from gcms_agent.fragmentation.ion_validation import validate_fragment_ion, validate_fragment_products
from gcms_agent.harness.compact import evidence_index
from gcms_agent.scoring.support import calculate_support


def graph(smiles):
    return graph_from_mol(Chem.MolFromSmiles(smiles))


def peaks(*values):
    return [{"mz": m, "intensity": 1., "lower": 1., "upper": 1.} for m in values]


@pytest.mark.parametrize("smiles,parent_mass", [
    ("C1=CC=NC=C1", 79), ("Cc1ccccn1", 93),
    ("Cc1cccnc1", 93), ("Cc1ccncc1", 93),
])
def test_removed_diagnostic_channels_are_not_emitted(smiles, parent_mass):
    g = parse_smiles(smiles)["graph"]
    original = deepcopy(g)
    query = peaks(*range(1, 100))
    result = enumeration.analyze_spectrum(g, query)
    assert result["complete"]
    assert result["supported_peaks"] == [parent_mass]
    assert "diagnostic_channels" not in result
    assert "diagnostic_version" not in result["diagnostics"]
    assert "diagnostic_channels" not in evidence_index(result)
    # Old stored diagnostics must not be reintroduced into model-facing views.
    assert "diagnostic_channels" not in evidence_index({**result, "diagnostic_channels": [{"channel_id": "retired"}]})
    assert calculate_support(query, result["supported_peaks"]) == calculate_support(query, [parent_mass])
    assert g == original


@pytest.mark.parametrize("bad_path", [
    {"rule_id": "pyridine_CHN_loss", "verified": True},
    {"rule_id": "molecular_ion", "verified": True, "used_for_scoring": False},
    {"rule_id": "molecular_ion", "verified": False},
    {"rule_id": "molecular_ion", "verified": True, "used_for_scoring": "false"},
])
def test_scoring_gate_rejects_diagnostics_even_if_injected_into_paths(monkeypatch, bad_path):
    monkeypatch.setattr(enumeration, "enumerate_paths", lambda *args: [{**bad_path, "nominal_mass": 52}])
    result = enumeration.analyze_spectrum(graph("n1ccccc1"), peaks(52))
    assert result["evidence"] == [] and result["supported_peaks"] == []
    assert "diagnostic_channels" not in result


@pytest.mark.parametrize("smiles,radicals", [("[CH3+]", 0), ("[CH2]=[CH2+]", 1)])
def test_ion_gate_accepts_charge_and_radicals_without_weakening_parent_gate(smiles, radicals):
    g = graph(smiles)
    assert not validate_graph(g)["valid"]
    result = validate_fragment_ion(g)
    assert result["valid"] and result["representation_valid"] and result["radicals"] == radicals
    assert not result["mechanism_confirmed"] and not result["used_for_scoring"]


def test_ion_gate_rejects_bad_valence_charge_and_disconnected_products():
    g = graph("[CH3+]")
    g["atoms"][0]["hydrogens"] = 5
    assert not validate_fragment_ion(g)["valid"]
    assert not validate_fragment_ion(graph("C"))["valid"]
    assert not validate_fragment_ion(graph("[CH3+].C"))["valid"]
    assert not validate_fragment_ion(graph("[CH3+]"), expected_charge=True)["valid"]


def dehydration():
    parent, ion, neutral = graph("CCO"), graph("[CH2]=[CH2+]"), graph("O")
    changes = [{"a": "Mol_C_1", "b": "Mol_C_2", "from": 1, "to": 2},
               {"a": "Mol_C_2", "b": "Mol_O_1", "from": 1, "to": 0}]
    return parent, ion, [neutral], {"Mol_C_1": -1, "Mol_O_1": 1}, changes


def test_explicit_products_check_mapping_h_transfer_bonds_and_charge():
    parent, ion, neutrals, h, bonds = dehydration()
    result = validate_fragment_products(parent, ion, neutrals, hydrogen_changes=h, bond_changes=bonds)
    assert result["valid"], result["errors"]
    assert result["atom_mapping"] == {a["id"]: a["id"] for a in parent["atoms"]}
    assert result["product_graph"] == ion and result["neutral_products"] == neutrals
    assert not result["mechanism_confirmed"] and not result["can_seed_next_step"]
    assert not validate_fragment_products(parent, ion, neutrals, hydrogen_changes={}, bond_changes=bonds)["valid"]
    assert not validate_fragment_products(parent, ion, neutrals, hydrogen_changes=h, bond_changes=[])["valid"]
    assert not validate_fragment_products(parent, ion, neutrals * 2, hydrogen_changes=h, bond_changes=bonds)["valid"]


def test_product_isotope_identity_cannot_be_swapped_even_if_formula_balances():
    parent, ion, neutrals, h, bonds = dehydration()
    parent["atoms"][0]["isotope"] = 13
    ion["atoms"][1]["isotope"] = 13
    result = validate_fragment_products(parent, ion, neutrals, hydrogen_changes=h, bond_changes=bonds)
    assert not result["valid"] and any("Element/isotope" in e for e in result["errors"])


def test_legal_graphs_with_wrong_hydrogen_balance_are_rejected():
    parent, ion, neutrals, h, bonds = dehydration()
    neutrals = [graph("[OH]")]
    result = validate_fragment_products(parent, ion, neutrals, hydrogen_changes={"Mol_C_1": -1}, bond_changes=bonds)
    assert not result["valid"] and any("accounting" in e for e in result["errors"])
