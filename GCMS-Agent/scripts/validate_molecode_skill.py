"""Offline chemical verification of the skill's synthetic teaching examples.

Checks the actual Markdown graphs and tool-call JSON, not just fixture graphs.
No model calls, test.json reads, or experimental labels are involved. Passing
means that the teaching material is consistent with the current tools; it does
not measure DeepSeek's comprehension or GC-MS inference accuracy.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

from rdkit import Chem, rdBase

from gcms_agent.chemistry.graph_checks import composition
from gcms_agent.chemistry.molecode_adapter import parse_graph, render_graph
from gcms_agent.chemistry.smiles_io import mol_from_graph
from gcms_agent.chemistry.validation import validate_graph
from gcms_agent.tools.molecular import validate_proposal
from gcms_agent.tools.server import check_base_version, check_operations

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def canonical(smiles):
    mol = Chem.MolFromSmiles(smiles)
    require(mol is not None, f"Invalid expected SMILES: {smiles}")
    return Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)


def document_cases(path):
    text = path.read_text(encoding="utf-8")
    parts = re.split(r"<!-- case:([a-z0-9_]+) -->", text)
    result = {}
    for index in range(1, len(parts), 2):
        cid, block = parts[index:index + 2]
        require(cid not in result, f"Duplicate documented case {cid}")
        graphs = re.findall(r"```mermaid\s*\n(.*?)\n```", block, re.S)
        require(len(graphs) == 1, f"{cid}: exactly one teaching graph required")
        calls = [json.loads(raw) for raw in re.findall(r"```json\s*\n(.*?)\n```", block, re.S)]
        result[cid] = {"graph": parse_graph(graphs[0]), "calls": calls}
    return result


def check_facts(graph, facts, validation):
    atoms = {atom["id"]: atom for atom in graph["atoms"]}
    adjacency = {aid: {} for aid in atoms}
    for bond in graph["bonds"]:
        adjacency[bond["a"]][bond["b"]] = bond["order"]
        adjacency[bond["b"]][bond["a"]] = bond["order"]
    require(composition(graph) == facts["composition"], "Heavy-atom counts differ")
    for aid, expected in facts.get("neighbors", {}).items():
        require(adjacency[aid] == expected, f"Wrong neighbor/bond map at {aid}")
    for aid, attributes in facts.get("atom_attributes", {}).items():
        for name, expected in attributes.items():
            require(atoms[aid][name] == expected, f"Wrong {aid}.{name}")
    for expected in facts.get("bond_attributes", []):
        bond = next(b for b in graph["bonds"] if (b["a"], b["b"]) == (expected["a"], expected["b"]))
        require(all(bond[k] == v for k, v in expected.items()), "Bond stereo/reference mismatch")
    for ring in facts.get("rings", []):
        require(len(ring) >= 3 and len(set(ring)) == len(ring), "Invalid ring path")
        for a, b in zip(ring, ring[1:] + ring[:1]):
            require(b in adjacency[a], f"Ring path includes nonexistent edge {a}-{b}")
    if "shared_ring_atoms" in facts:
        shared = set.intersection(*(set(ring) for ring in facts["rings"]))
        require(shared == set(facts["shared_ring_atoms"]), "Wrong fused-ring atom intersection")
    if "formula" in facts:
        require(validation["formula"] == facts["formula"], "Wrong molecular formula")
    if validation["valid"]:
        mol = mol_from_graph(graph)
        ids = [atom.GetProp("_gcms_atom_id") for atom in mol.GetAtoms()]
        if "rings" in facts:
            actual = {frozenset(ids[i] for i in ring) for ring in Chem.GetSymmSSSR(mol)}
            expected = {frozenset(ring) for ring in facts["rings"]}
            require(actual == expected, "Ring membership differs from teaching answer")
        for substructure in facts.get("substructures", []):
            query = Chem.MolFromSmarts(substructure["smarts"])
            require(query is not None, "Invalid oracle SMARTS")
            actual = {frozenset(ids[i] for i in match) for match in mol.GetSubstructMatches(query)}
            expected = {frozenset(group) for group in substructure["atom_sets"]}
            require(actual == expected, f"Wrong {substructure['name']} atom assignment")


def verify():
    fixture_path = ROOT / "tests/fixtures/molecode_skill_cases.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    require(fixture["schema_version"] == 1, "Unsupported teaching-fixture version")
    cases = fixture["cases"]
    ids = [case["id"] for case in cases]
    require(len(set(ids)) == len(ids), "Duplicate fixture IDs")
    example_root = ROOT / "skills/molecode-graph-reasoning/examples"
    documents = {path.name: document_cases(path) for path in sorted(example_root.glob("*.md"))}
    documented = [(name, cid) for name, content in documents.items() for cid in content]
    require(set(documented) == {(case["document"], case["id"]) for case in cases},
            "Documented teaching cases and independent fixture set differ")
    results = []
    for case in cases:
        try:
            graph = parse_graph(case["molecode"])
            shown = documents[case["document"]][case["id"]]
            require(shown["graph"] == graph, "Markdown graph differs from full fixture metadata")
            require(parse_graph(render_graph(graph)) == graph, "MoleCode round-trip loses attributes")
            validation = validate_graph(graph)
            require(validation["valid"] is case["initial_valid"], "Unexpected initial validity")
            if validation["valid"]:
                require(validation["canonical_smiles"] == canonical(case["source_smiles"]),
                        "Initial graph no longer represents its independent source structure")
            check_facts(graph, case["facts"], validation)
            trials = case["trials"]
            require(shown["calls"] == [trial["request"] for trial in trials],
                    "Documented tool arguments differ from verified proposals")
            before = render_graph(graph)
            trial_results = []
            for trial in trials:
                request = trial["request"]
                check_base_version(request, {"graph_version": 0})
                check_operations(request["operations"], graph)
                try:
                    outcome = validate_proposal(graph, request["operations"])
                    valid, error = outcome["valid"], "; ".join(outcome["errors"])
                except ValueError as exc:
                    outcome, valid, error = None, False, str(exc)
                require(valid is trial["valid"], f"Unexpected trial validity: {error}")
                if trial.get("error_contains"):
                    require(trial["error_contains"] in error, f"Wrong rejection reason: {error}")
                if trial.get("expected_smiles"):
                    require(valid and outcome["canonical_smiles"] == canonical(trial["expected_smiles"]),
                            "Trial produced a different molecular identity")
                    require(outcome["composition"] == composition(graph), "Trial changed heavy atoms")
                require(render_graph(graph) == before, "Trial mutated the original graph")
                trial_results.append("valid" if valid else "rejected_as_expected")
            results.append({"case_id": case["id"], "passed": True, "trials": trial_results})
        except Exception as exc:
            results.append({"case_id": case["id"], "passed": False, "error": str(exc)})
    return {"cases": len(results), "passed": sum(row["passed"] for row in results),
            "documents": len(documents), "uses_api": False, "uses_experimental_labels": False,
            "scope": "Teaching-graph facts and tool compatibility; not LLM comprehension accuracy",
            "results": results}


if __name__ == "__main__":
    with rdBase.BlockLogs():
        report = verify()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["passed"] == report["cases"] else 1)
