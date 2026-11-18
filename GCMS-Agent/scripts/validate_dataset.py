"""Label-free whole-dataset conversion/validity audit with unique-structure cache."""
import argparse
from collections import Counter
from rdkit import RDLogger
from gcms_agent.config import write_json
from gcms_agent.data.loader import load_inputs
from gcms_agent.chemistry.smiles_io import parse_smiles
from gcms_agent.chemistry.molecode_adapter import parse_graph, render_graph

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("--output", default="runs/dataset_validation.json")
    args=parser.parse_args()
    RDLogger.DisableLog("rdApp.*")
    cache,counts,failures={},Counter(),[]
    for row in load_inputs(args.input):
        counts["samples"]+=1
        for smiles in row["generated"]:
            if smiles not in cache:
                parsed=parse_smiles(smiles)
                raw_roundtrip=False
                if parsed["graph"] is not None:
                    raw_roundtrip=parse_graph(render_graph(parsed["graph"]))==parsed["graph"]
                cache[smiles]={"graph_available":parsed["graph"] is not None,"valid":parsed["valid"],"raw_roundtrip":raw_roundtrip}
                if parsed["graph"] is None or not raw_roundtrip:
                    failures.append({"sample_id":row["sample_id"],"smiles":smiles,"errors":parsed["errors"]})
            item=cache[smiles]
            counts["candidate_slots"]+=1
            counts["graphs_available"]+=item["graph_available"]
            counts["raw_graph_roundtrips"]+=item["raw_roundtrip"]
            counts["final_constraint_valid"]+=item["valid"]
    result={"counts":dict(counts),"unique_candidates":len(cache),"unique_final_constraint_valid":sum(x["valid"] for x in cache.values()),"failures":failures,"uses_labels":False}
    write_json(args.output,result)
    print(result)

if __name__=="__main__":
    main()
