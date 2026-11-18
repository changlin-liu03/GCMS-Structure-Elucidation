import json
import hashlib
import runpy
from copy import deepcopy

import pytest

from gcms_agent.data.intensity import raw_peaks, peaks_from_input
from gcms_agent.data.loader import load_inputs, validate_input
from gcms_agent.data.prepare import prepare
from gcms_agent.scoring.support import calculate_support
from gcms_agent.harness.compact import compact_batch


@pytest.mark.parametrize("masses,values", [
    ([], []), ([10], []), ([10, 10], [1, 2]), ([10.5], [1]), ([True], [1]),
    ([0], [1]), ([10], [True]), ([10], [-1]), ([10], [float('nan')]),
    ([10], [float('inf')]), ([10], [0]), ([10], ['1']),
])
def test_invalid_measurements_rejected(masses, values):
    with pytest.raises(ValueError):
        raw_peaks(masses, values)


def test_raw_values_precision_zeros_and_scale_invariance():
    mz, values = [40, 10, 30, 20], [0, .125, 999., 1e-8]
    peaks = raw_peaks(mz, values)
    assert [p['mz'] for p in peaks] == [10, 20, 30]
    assert [p['raw_intensity'] for p in peaks] == [.125, 1e-8, 999.]
    assert [p['intensity'] for p in peaks] == [.125, 1e-8, 999.]
    assert all(p['lower'] == p['upper'] == p['intensity'] for p in peaks)
    score = calculate_support(peaks, [10, 10, 40])
    assert score['score'] == pytest.approx(.125 / sum(values))
    assert score['supported_peaks'] == [10]
    assert score['score_lower'] == score['score_upper'] == score['score']
    for factor in (1e-100, .1, 1000., 1e100):
        scaled = calculate_support(raw_peaks(mz, [v * factor for v in values]), [10])
        assert scaled['score'] == pytest.approx(score['score'])
        assert [p['weight'] for p in scaled['peak_contributions']] == pytest.approx(
            [p['weight'] for p in score['peak_contributions']])


def test_small_integer_intensities_are_linear_not_encoding_guesses():
    peaks = peaks_from_input({'input_ids': [10, 20], 'intensity': [2, 29]})
    assert calculate_support(peaks, [10])['score'] == pytest.approx(2 / 31)
    for row in ({'input_ids': [10], 'position_ids': [29]},
                {'input_ids': [10], 'intensity': [29], 'position_ids': [29]},
                {'input_ids': [10], 'intensity': [29], 'conditions': {'intensity_encoding': 'spectus'}}):
        with pytest.raises(ValueError):
            peaks_from_input(row)


def test_prepare_isolation_provenance_and_no_overwrite(tmp_path):
    source = tmp_path / 'source.json'
    record = {'input_ids': [10, 20], 'intensity': [.25, 2.5], 'generated': ['CC'] * 10,
              'true_smiles': 'LABEL_SENTINEL'}
    source.write_text(json.dumps([record]), encoding='utf-8')
    dest = tmp_path / 'prepared'
    manifest = prepare(source, dest)
    rows = load_inputs(dest / 'inference/all.json')
    assert rows[0]['intensity'] == record['intensity']
    assert manifest['source_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert manifest['intensity_encoding'] == 'raw_linear'
    assert 'LABEL_SENTINEL' not in (dest / 'inference/all.json').read_text()
    assert 'LABEL_SENTINEL' in (dest / 'evaluation/labels.json').read_text()
    original = deepcopy(rows)
    with pytest.raises(ValueError, match='already exists'):
        prepare(source, dest)
    assert load_inputs(dest / 'inference/all.json') == original
    with pytest.raises(ValueError, match='Re-run audit'):
        validate_input({'sample_id': 'legacy', 'position_ids': [29]})


def test_compact_and_score_share_full_spectrum_weights():
    peaks = raw_peaks([10, 20, 30], [.125, 999, 3.75])
    view = compact_batch({'sample_id': 'raw', 'peaks': peaks, 'candidates': []})
    score = calculate_support(peaks, [10, 20])
    assert view['spectrum_weights'] == pytest.approx([p['weight'] for p in score['peak_contributions']])
    assert sum(view['spectrum_weights']) == pytest.approx(1)


def test_development_cases_use_only_current_dataset(tmp_path):
    from gcms_agent.config import PROJECT_ROOT, write_json
    prepare_cases = runpy.run_path(str(PROJECT_ROOT / 'scripts/prepare_development_cases.py'))['prepare_cases']
    source = tmp_path / 'source.json'
    record = {'input_ids': [10, 20], 'intensity': [2, 9], 'generated': ['CC'] * 10,
              'true_smiles': 'CC'}
    write_json(source, [record])
    dest = tmp_path / 'current'
    prepare(source, dest)
    prepare_cases(dest, source, single_index=0, pilot_indices=[0])
    single = load_inputs(dest / 'inference/single_top10_miss.json')[0]
    selection = json.loads((dest / 'manifests/single_top10_miss_selection.json').read_text())
    assert selection['sample_id'] == single['sample_id']
    assert 'legacy_sample_id' not in selection
    assert not (dest / 'manifests/sample_id_mapping.json').exists()
    assert single['intensity'] == [2, 9]
    assert load_inputs(dest / 'inference/engineering_pilot.json') == [single]
    with pytest.raises(ValueError, match='indices'):
        prepare_cases(dest, source, pilot_indices=[1])
    record['generated'][0] = 'CCC'
    write_json(source, [record])
    with pytest.raises(ValueError, match='Source differs'):
        prepare_cases(dest, source, pilot_indices=[0])
