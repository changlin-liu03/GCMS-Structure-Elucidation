"""Derive a new ranking/report from frozen predictions; never invoke a model."""
import hashlib
import json
from pathlib import Path
from gcms_agent.config import write_json
from gcms_agent.scoring.ranking import rank_candidates
from gcms_agent.reporting.export import export_sample
from gcms_agent.workflow.runner import source_versions
from gcms_agent.evaluation.runner import evaluate

def main():
    project = Path(__file__).resolve().parents[3]
    source = project / 'runs/case84_20261113_validation'
    original = source / 'agent'
    dest = project / 'runs/case84_20261113_count_rerank'
    freeze = json.loads((original/'prediction_freeze.json').read_text(encoding='utf-8'))
    for name, expected in freeze['files'].items():
        assert hashlib.sha256((original/name).read_bytes()).hexdigest() == expected, name
    result_path = next((original/'results').glob('*.json'))
    saved = json.loads(result_path.read_text(encoding='utf-8'))
    rank = rank_candidates(saved['ranked_candidates'])
    result = {**saved, **rank, 'ranking_complete': saved['ranking_complete'], 'ranking_status':saved['ranking_status']}
    assert result['top1']['final_smiles'] == 'N#CC=CC=CC#N'
    assert result['top1']['source_candidate_count'] == 6
    assert result['top1']['independent_candidate_count'] == 3
    old_rows = {r['candidate_id']:r for r in saved['ranked_candidates']}
    for row in result['ranked_candidates']:
        for field in ('final_smiles','score','evidence','history','stop_reason'):
            assert row.get(field) == old_rows[row['candidate_id']].get(field), (row['candidate_id'],field)
    assert result['resource_usage'] == saved['resource_usage']
    dest.mkdir(exist_ok=False)
    for name in ('input.json','labels.json'):
        (dest/name).write_bytes((source/name).read_bytes())
    write_json(dest/'manifest.json', {
        'run_id':dest.name,'mode':'frozen_prediction_rerank','backend':'deterministic_postprocessing',
        'sample_ids':[saved['sample_id']], 'ranking_policy':'top_score_source_count_v1',
        'source_result':str(result_path),'source_result_sha256':hashlib.sha256(result_path.read_bytes()).hexdigest(),
        'source_manifest_sha256':hashlib.sha256((original/'manifest.json').read_bytes()).hexdigest(),
        'versions':source_versions(),'new_model_calls':0,'additional_model_cost_cny':0,
        'policy':'Only ordering/selection and derived report metadata changed. Original scientific predictions and incomplete status retained.'})
    export_sample(dest,result)
    files={str(p.relative_to(dest)):hashlib.sha256(p.read_bytes()).hexdigest()
           for folder in ('results','candidates','evidence','reports') for p in (dest/folder).rglob('*') if p.is_file()}
    write_json(dest/'prediction_freeze.json',{'files':files,'policy':'Reranked outputs frozen before evaluation'})
    ev=evaluate(dest,dest/'labels.json',dest/'input.json')
    write_json(dest/'verification.json',{'source_freeze_verified':True,'candidate_predictions_scores_evidence_history_unchanged':True,
        'top1':result['top1']['final_smiles'],'source_votes':6,'independent_candidates':3,
        'ranking_status':result['ranking_status'],'new_model_calls':0,
        'requested_set':ev['requested_set'],'strict_end_to_end_requested_set':ev['strict_end_to_end_requested_set']})
    print(json.dumps({'directory':str(dest),'top1':result['top1'],'evaluation':ev['requested_set']},ensure_ascii=False))


if __name__ == "__main__":
    main()
