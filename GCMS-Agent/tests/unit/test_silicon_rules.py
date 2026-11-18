from copy import deepcopy
import json

import pytest

from gcms_agent.chemistry.smiles_io import parse_smiles
from gcms_agent.fragmentation.rule_engine import enumerate_paths, load_rule_policy
from gcms_agent.fragmentation.silicon import SILICON_RULE_IDS
from gcms_agent.fragmentation.ion_validation import validate_fragment_products
from gcms_agent.fragmentation.enumeration import analyze_spectrum
from gcms_agent.scoring.support import calculate_support


def paths(smiles):
    parsed = parse_smiles(smiles)
    assert parsed['valid'], parsed
    return parsed['graph'], [p for p in enumerate_paths(parsed['graph'], load_rule_policy())
                             if p['rule_id'] in SILICON_RULE_IDS]


@pytest.mark.parametrize('smiles', ['CN(C)[Si](C)(C)C', 'CO[Si](C)(C)C',
                                   'CS[Si](C)(C)C', 'CC[Si](C)(C)C'])
def test_direct_tms_products(smiles):
    graph, found = paths(smiles)
    assert {p['rule_id'] for p in found} == SILICON_RULE_IDS
    assert len(found) == 4
    for p in found:
        assert p['radicals'] == 0 and p['neutral_losses'][0]['radicals'] == 1
        audit = validate_fragment_products(graph, p['product_graph'], p['neutral_products'],
                                           hydrogen_changes=p['hydrogen_changes'], bond_changes=p['bond_changes'])
        assert audit['valid'], audit['errors']
        assert p['mechanism_confirmed'] is False
        if p['rule_id'] == 'tms_cation_cleavage':
            assert p['nominal_mass'] == 73 and p['ion_formula'] == 'C3H9Si'
        else:
            assert p['neutral_losses'][0]['formula'] == 'CH3'


@pytest.mark.parametrize('smiles', ['CCCC', 'CC[Si](C)(C)CC', 'C[SiH](C)C',
                                   'C[Si](C)(C)Cl', 'C[Si](C)(C)[Si](C)(C)C',
                                   'C[Si]1(C)CCC1', 'C=C[Si](C)(C)C',
                                   'C[Si](C)(C)c1ccccc1'])
def test_out_of_scope_motifs_do_not_emit(smiles):
    assert paths(smiles)[1] == []


def test_isotopes_are_mapped_not_fixed_mass():
    _, found = paths('CO[29Si](C)(C)C')
    assert {p['nominal_mass'] for p in found if p['rule_id'] == 'tms_cation_cleavage'} == {74}
    _, labelled = paths('CO[Si]([13CH3])(C)C')
    assert {p['nominal_mass'] for p in labelled if p['rule_id'] == 'tms_cation_cleavage'} == {74}
    losses = [p for p in labelled if p['rule_id'] == 'tms_methyl_radical_loss']
    assert {p['nominal_mass'] for p in losses} == {89, 90}
    assert any('[13C]' in p['neutral_losses'][0]['formula'] for p in losses)


def test_multiple_tms_and_duplicate_paths_count_peak_once(tmp_path):
    graph, found = paths('C[Si](C)(C)OCCO[Si](C)(C)C')
    assert len([p for p in found if p['rule_id'] == 'tms_cation_cleavage']) == 2
    peaks = [{'mz': 73, 'intensity': 100}, {'mz': 59, 'intensity': 100}]
    result = analyze_spectrum(graph, peaks)
    assert result['complete'] and result['supported_peaks'] == [73]
    assert calculate_support(peaks, result['supported_peaks'])['score'] == .5
    policy = load_rule_policy()
    policy['enabled'] = [r for r in policy['enabled'] if r not in SILICON_RULE_IDS]
    path = tmp_path / 'disabled.json'
    path.write_text(json.dumps(policy), encoding='utf-8')
    assert analyze_spectrum(graph, peaks, path)['supported_peaks'] == []


def test_case870_correct_and_wrong_isomers_have_same_direct_support():
    peaks = [{'mz': m, 'intensity': 1} for m in [59, 73, 75, 102, 117, 147]]
    for smiles in ['CN(C)[Si](C)(C)C', 'CCN[Si](C)(C)C']:
        result = analyze_spectrum(parse_smiles(smiles)['graph'], peaks)
        assert result['supported_peaks'] == [73, 102, 117]
        assert result['complete']


def test_invalid_product_and_path_budget_are_not_scored():
    graph, found = paths('CN(C)[Si](C)(C)C')
    p = found[0]
    bad = deepcopy(p['product_graph'])
    bad['atoms'][0]['hydrogens'] += 1
    assert not validate_fragment_products(graph, bad, p['neutral_products'],
                                         hydrogen_changes={}, bond_changes=p['bond_changes'])['valid']
    policy = load_rule_policy()
    policy['max_paths'] = 1
    with pytest.raises(ValueError, match='path budget'):
        enumerate_paths(graph, policy)


def test_reordered_input_preserves_products():
    graph, found = paths('CN(C)[Si](C)(C)C')
    other = deepcopy(graph)
    other['atoms'].reverse()
    other['bonds'].reverse()
    signature = lambda ps: sorted((p['rule_id'], p['nominal_mass'], tuple(p['atom_ids'])) for p in ps
                                  if p['rule_id'] in SILICON_RULE_IDS)
    assert signature(found) == signature(enumerate_paths(other, load_rule_policy()))
