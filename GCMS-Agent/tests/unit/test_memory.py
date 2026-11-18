import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from gcms_agent.memory import ExperienceMemory, KnowledgeMemory, WorkingMemory
from gcms_agent.schemas import CandidateResult, MolecularGraph, SupportScore
from gcms_agent.workflow.checkpoint import Checkpoints


def curated(case_id="case1", sample_id="train1", **changes):
    case = {"case_id": case_id, "sample_id": sample_id, "split": "train",
            "verified": True, "verified_by": "reviewer-a", "source": "curated-fixture",
            "tags": ["carbonyl"], "content": {"summary": "Synthetic reviewed rule example; not a test answer."}}
    case.update(changes)
    return case


class MemoryTests(unittest.TestCase):
    def test_working_memory_scope_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Checkpoints(Path(temp))
            a, b = WorkingMemory(store, "sample_a"), WorkingMemory(store, "sample_b")
            a.save("c1", {"candidate_id": "c1", "graph_version": 2, "seen": ["CC"]})
            restored = a.load("c1")
            self.assertEqual(restored["graph_version"], 2)
            self.assertIsNone(b.load("c1"))
            restored["seen"].append("CCC")
            self.assertEqual(a.load("c1")["seen"], ["CC"])

    def test_working_memory_rejects_labels_and_path_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            memory = WorkingMemory(Checkpoints(temp), "s1")
            for state in [{"history": [{"true_smiles": "CC"}]}, {"api_key": "not-a-key"},
                          {"sample_id": "different"}, {"candidate_id": "different"}]:
                with self.subTest(state=state), self.assertRaises(ValueError):
                    memory.save("c1", state)
            with self.assertRaises(ValueError):
                memory.load("../labels")
            with self.assertRaises(ValueError):
                WorkingMemory(Checkpoints(temp), "../test")

    def test_corrupted_scope_is_not_restored(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Checkpoints(temp)
            memory = WorkingMemory(store, "s1")
            memory.save("c1", {"rounds_completed": 1})
            path = next(Path(temp).glob("*.json"))
            envelope = json.loads(path.read_text(encoding="utf-8"))
            envelope["state"]["sample_id"] = "s2"
            path.write_text(json.dumps(envelope), encoding="utf-8")
            with self.assertRaises(ValueError):
                memory.load("c1")

    def test_knowledge_is_frozen_and_versioned(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw = "固定规则说明".encode("utf-8")
            (root / "rule.md").write_bytes(raw)
            manifest = {"rule": {"path": "rule.md", "version": "v1",
                                  "sha256": hashlib.sha256(raw).hexdigest(), "source": "synthetic-test"}}
            memory = KnowledgeMemory(root, manifest)
            (root / "rule.md").write_text("changed", encoding="utf-8")
            self.assertEqual(memory.read("rule")["text"], "固定规则说明")
            self.assertEqual(memory.manifest()["rule"]["version"], "v1")
            with self.assertRaises(ValueError):
                KnowledgeMemory(root, manifest)
            with self.assertRaises(ValueError):
                memory.read("../rule.md")

    def test_knowledge_escape_and_size_budget_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "knowledge"
            root.mkdir()
            (base / "outside.md").write_text("outside", encoding="utf-8")
            entry = {"path": "../outside.md", "version": "v1", "source": "test",
                     "sha256": hashlib.sha256(b"outside").hexdigest()}
            with self.assertRaises(ValueError):
                KnowledgeMemory(root, {"escape": entry})
            (root / "large.md").write_text("12345", encoding="utf-8")
            entry.update(path="large.md", sha256=hashlib.sha256(b"12345").hexdigest())
            with self.assertRaises(ValueError):
                KnowledgeMemory(root, {"large": entry}, max_reference_chars=4)

    def test_experience_disabled_without_reading_file(self):
        memory = ExperienceMemory.from_file("nonexistent/no-dataset.json")
        self.assertFalse(memory.enabled)
        self.assertEqual(memory.retrieve(current_sample_id="s1", tags=["carbonyl"]), [])
        with self.assertRaises(ValueError):
            ExperienceMemory([curated()])

    def test_experience_rejects_test_and_unverified_cases(self):
        for changes in [{"split": "test"}, {"split": "validation"}, {"verified": False},
                        {"verified": 1}, {"verified_by": ""}, {"source": ""},
                        {"content": {"true_smiles": "CC"}}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                ExperienceMemory([curated(**changes)], enabled=True)

    def test_experience_excludes_current_sample_and_duplicate_spectrum(self):
        memory = ExperienceMemory([
            curated("self", "current"),
            curated("same_spectrum", "other", spectrum_hash="hash-current"),
            curated("same_structure", "third", structure_key="structure-excluded"),
            curated("eligible", "dev_sample", split="dev"),
        ], enabled=True)
        found = memory.retrieve(current_sample_id="current", tags=["carbonyl"],
                                current_spectrum_hash="hash-current", excluded_structure_keys=["structure-excluded"])
        self.assertEqual([r["case_id"] for r in found], ["eligible"])
        found[0]["content"]["summary"] = "mutated"
        self.assertNotEqual(memory.retrieve(current_sample_id="current", tags=["carbonyl"])[0]["content"]["summary"], "mutated")

    def test_experience_retrieval_budget_and_topic_filter(self):
        memory = ExperienceMemory([curated(f"case{i}", f"sample{i}") for i in range(5)],
                                  enabled=True, max_results=2)
        self.assertEqual(len(memory.retrieve(current_sample_id="current", tags=["carbonyl"])), 2)
        self.assertEqual(memory.retrieve(current_sample_id="current", tags=["unrelated"]), [])

    def test_shared_types_do_not_change_dict_runtime(self):
        graph = MolecularGraph(atoms=[], bonds=[])
        result = CandidateResult(candidate_id="c1", final_smiles=None, score=None)
        self.assertIsInstance(graph, dict)
        self.assertIsNone(result["score"])
        self.assertIn("score_lower", SupportScore.__annotations__)


if __name__ == "__main__":
    unittest.main()
