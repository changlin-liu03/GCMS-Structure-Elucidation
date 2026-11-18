from gcms_agent.evaluation.metrics import canonical

def original_order_metrics(inputs, labels):
    truth = {x["sample_id"]: canonical(x["true_smiles"]) for x in labels}
    count=first=top10=0
    reciprocal=0.0
    for row in inputs:
        target = truth.get(row["sample_id"])
        if not target:
            continue
        count += 1
        positions = [i+1 for i,s in enumerate(row["generated"]) if canonical(s) == target]
        first += int(bool(positions) and positions[0] == 1)
        top10 += int(bool(positions))
        reciprocal += 1/positions[0] if positions else 0
    return {"samples":count, "acc_at_1":first/count if count else None, "acc_at_10":top10/count if count else None, "mrr":reciprocal/count if count else None,
            "policy":"Original ten slots, including invalid candidates and duplicates; no validity filtering"}
