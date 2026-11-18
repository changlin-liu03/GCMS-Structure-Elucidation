"""Offline, fixed-candidate ablation. Labels never enter an Agent invocation."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from rdkit import RDLogger
from gcms_agent.chemistry.smiles_io import parse_smiles
from gcms_agent.config import write_json
from gcms_agent.data.intensity import peaks_from_input
from gcms_agent.data.loader import load_inputs
from gcms_agent.evaluation.metrics import canonical, evaluate_results
from gcms_agent.fragmentation.enumeration import analyze_spectrum
from gcms_agent.fragmentation.rule_engine import enumerate_paths, load_rule_policy
from gcms_agent.fragmentation.silicon import SILICON_RULE_IDS
from gcms_agent.scoring.ranking import rank_candidates
from gcms_agent.scoring.support import calculate_support


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--labels', type=Path, required=True)
    parser.add_argument('--old-policy', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(exist_ok=False, parents=True)
    RDLogger.DisableLog('rdApp.*')
    rows = load_inputs(args.input)
    labels = json.loads(args.labels.read_text(encoding='utf-8'))
    truth = {r['sample_id']: r['true_smiles'] for r in labels}
    old, new = load_rule_policy(args.old_policy), load_rule_policy()
    assert set(new['enabled']) - set(old['enabled']) == SILICON_RULE_IDS
    policies = {'old': old, 'methyl_only': {**new, 'enabled': old['enabled'] + ['tms_methyl_radical_loss']},
                'cation_only': {**new, 'enabled': old['enabled'] + ['tms_cation_cleavage']}, 'new': new}
    policy_files = {}
    for name, policy in policies.items():
        policy_files[name] = args.output / f'{name}_rules.json'
        write_json(policy_files[name], policy)
    # Select before scoring, from inference only; no top-hit or label filtering.
    silicon = [r for r in rows if any('Si' in s for s in r['generated'])]
    split = {r['sample_id']: ('development' if r['sample_id'].startswith('s00870_') else
             'validation' if int(hashlib.sha256(r['sample_id'].encode()).hexdigest(), 16) % 5 == 0
             else 'regression') for r in silicon}
    write_json(args.output/'selection.json', {'input_sha256': hashlib.sha256(args.input.read_bytes()).hexdigest(),
               'policy': 'All inference rows containing Si candidate text; SHA256(sample_id)%5==0 validation except known case870. No outcome-based selection. No rule tuning after this split.',
               'split': split})
    parsed = {}
    def parse(s):
        if s not in parsed:
            parsed[s] = parse_smiles(s)
        return parsed[s]
    checked, skipped = set(), set()
    for row in rows:
        for smiles in row['generated']:
            if smiles in checked or smiles in skipped:
                continue
            p = parse(smiles)
            if not p['valid'] or any(a['element'] == 'Si' for a in p['graph']['atoms']):
                skipped.add(smiles)
                continue
            assert enumerate_paths(p['graph'], old) == enumerate_paths(p['graph'], new), smiles
            checked.add(smiles)
    print(json.dumps({'non_si_unique_structures_unchanged': len(checked), 'silicon_cases': len(silicon)}), flush=True)
    results = {name: [] for name in policies}
    details = []
    for index, row in enumerate(silicon):
        peaks = peaks_from_input(row)
        cache = {}
        def assess(smiles, name):
            key = smiles, name
            if key not in cache:
                p = parse(smiles)
                if not p['valid']:
                    cache[key] = {'valid': False, 'score': None}
                else:
                    e = analyze_spectrum(p['graph'], peaks, policy_files[name])
                    assert e['complete'], (row['sample_id'], smiles, e['diagnostics'])
                    cache[key] = {'valid': True, 'score': calculate_support(peaks, e['supported_peaks']),
                                  'evidence': e}
            return cache[key]
        case = {'sample_id': row['sample_id'], 'split': split[row['sample_id']], 'policies': {}}
        for name in policies:
            candidates = []
            for slot, smiles in enumerate(row['generated']):
                a = assess(smiles, name)
                candidates.append({'candidate_id': f'c{slot+1:02}', 'original_smiles': smiles,
                                   'final_smiles': canonical(smiles) if a['valid'] else None,
                                   'validation': {'valid': a['valid']}, 'score': a['score'],
                                   'evidence': a.get('evidence', {})})
            ranked = rank_candidates(candidates)
            ranked['sample_id'] = row['sample_id']
            results[name].append(ranked)
            target = assess(truth[row['sample_id']], name)
            case['policies'][name] = {'top1': (ranked['top1'] or {}).get('final_smiles'),
                'top1_correct': canonical((ranked['top1'] or {}).get('final_smiles')) == canonical(truth[row['sample_id']]),
                'truth_coverage': target['score']['score'], 'truth_supported_peaks': target['score']['supported_peaks'],
                'candidates': [{'smiles': c['final_smiles'], 'score': (c['score'] or {}).get('score'),
                                'supported_peaks': (c['score'] or {}).get('supported_peaks')} for c in candidates]}
        details.append(case)
        if index % 50 == 0:
            print(json.dumps({'processed': index+1, 'total': len(silicon)}), flush=True)
    summary = {'non_si_unique_structures_unchanged': len(checked), 'silicon_cases': len(silicon),
               'split_counts': dict(Counter(split.values())), 'metrics': {}, 'transitions': {}}
    for subset in ('all', 'development', 'regression', 'validation'):
        chosen = [c for c in details if subset == 'all' or c['split'] == subset]
        summary['metrics'][subset] = {name: evaluate_results([r for r in rr if subset == 'all' or split[r['sample_id']] == subset], labels)
                                      for name, rr in results.items()}
        summary['transitions'][subset] = {
            'top1_improved': sum(not c['policies']['old']['top1_correct'] and c['policies']['new']['top1_correct'] for c in chosen),
            'top1_damaged': sum(c['policies']['old']['top1_correct'] and not c['policies']['new']['top1_correct'] for c in chosen),
            'truth_coverage_increased': sum(c['policies']['new']['truth_coverage'] > c['policies']['old']['truth_coverage'] for c in chosen)}
    summary['interpretation'] = 'Fixed-candidate reranking only; truth scoring is offline diagnosis, never supplied to inference. Coverage is not probability or false-positive rate. This dataset is not an external benchmark.'
    write_json(args.output/'cases.json', details)
    write_json(args.output/'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
