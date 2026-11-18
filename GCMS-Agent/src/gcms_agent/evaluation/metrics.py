from rdkit import Chem, rdBase
from gcms_agent.chemistry.smiles_io import parse_smiles

def canonical(smiles):
    with rdBase.BlockLogs():
        mol = Chem.MolFromSmiles(smiles) if smiles else None
    return Chem.MolToSmiles(mol, isomericSmiles=True) if mol is not None else None

def evaluate_results(results, labels):
    truth = {r["sample_id"]: canonical(r["true_smiles"]) for r in labels}
    n=hits1=hits10=valid=total=emitted=correct_before=damaged=repairs=invalid_before=0
    reciprocal=0.0
    for result in results:
        target = truth.get(result["sample_id"])
        if not target:
            continue
        n += 1
        unique = result["unique_ranking"]
        matches = [i+1 for i,g in enumerate(unique) if canonical(g["final_smiles"]) == target]
        # An explicit null top1 is an abstention, even when the display array
        # happens to place the reference first (e.g. every score is zero).
        top1 = result.get("top1", unique[0] if unique else None)
        hits1 += int(bool(top1) and canonical(top1.get("final_smiles")) == target)
        hits10 += int(bool(matches) and matches[0] <= 10)
        reciprocal += 1/matches[0] if matches else 0
        for c in result["ranked_candidates"]:
            total += 1
            final = canonical(c.get("final_smiles"))
            emitted += int(c.get("final_smiles") is not None)
            final_valid = bool(final and parse_smiles(c["final_smiles"])["valid"])
            valid += int(final_valid)
            before = canonical(c["original_smiles"])
            if before == target:
                correct_before += 1
                damaged += int(final != target)
            if not c.get("initial_validation", {}).get("valid", before is not None):
                invalid_before += 1
                repairs += int(final_valid)
    return {"samples": n, "acc_at_1": hits1/n if n else None, "acc_at_10": hits10/n if n else None, "mrr": reciprocal/n if n else None,
            "valid_candidate_fraction": valid/total if total else None, "output_smiles_validity": valid/emitted if emitted else None,
            "repair_success_rate": repairs/invalid_before if invalid_before else None, "damaged_correct_candidates": damaged,
            "original_correct_candidates": correct_before, "tie_policy": "use recorded top1 and unique_ranking order; no optimistic tie credit",
            "ranking_policies": sorted({r.get("ranking_diagnostics", {}).get("ranking_policy", "legacy_canonical_display") for r in results}), "total_candidate_slots": total}
