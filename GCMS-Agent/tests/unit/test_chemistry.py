import copy
import unittest

from rdkit import Chem

from gcms_agent.chemistry.graph_edit import apply_patch
from gcms_agent.chemistry.molecode_adapter import parse_graph
from gcms_agent.chemistry.smiles_io import graph_from_mol, mol_from_graph, parse_smiles, repair_smiles_syntax
from gcms_agent.chemistry.validation import validate_graph


class ChemistryTests(unittest.TestCase):
    def test_regular_roundtrips(self):
        for smiles in ["CCO", "c1ccccc1", "CC(=O)O", "C[N+](=O)[O-]", "C[Si](C)(C)O", "COP(=O)(O)O", "CCl", "CBr"]:
            with self.subTest(smiles=smiles):
                parsed = parse_smiles(smiles)
                self.assertTrue(parsed["valid"], parsed["errors"])
                self.assertEqual(parse_graph(parsed["molecode"]), parsed["graph"])

    def test_invalid_but_graph_parseable(self):
        for smiles in ["C(C)(C)(C)(C)C", "c1cccc1", "ncc1ncnc(N)c1N"]:
            with self.subTest(smiles=smiles):
                parsed = parse_smiles(smiles)
                self.assertFalse(parsed["valid"])
                self.assertIsNotNone(parsed["graph"])
                self.assertTrue(parsed["molecode"].startswith("graph TB"))
                self.assertIsNone(parsed["canonical_smiles"])

    def test_syntax_failure_does_not_invent_graph(self):
        for smiles in ["C1CC", "CC(=O", "", None]:
            result = parse_smiles(smiles)
            self.assertFalse(result["valid"])
            self.assertIsNone(result["graph"])

    def test_safe_syntax_cleanup_and_suffix_rejection(self):
        wrapped = repair_smiles_syntax("  ```smiles\nCCO\n```  ")
        self.assertTrue(wrapped["valid"])
        self.assertEqual(wrapped["canonical_smiles"], "CCO")
        self.assertEqual(wrapped["syntax_cleanup"], ["trim_outer_whitespace", "remove_complete_smiles_code_fence"])
        for value in ["CCO ethanol", "CCO\nC", "```smiles\nC1CC\n```", "CCO |foo|"]:
            result = repair_smiles_syntax(value)
            self.assertFalse(result["valid"])
            self.assertIsNone(result["graph"])

    def test_minimal_graph_defaults_do_not_break_roundtrip(self):
        graph = {"atoms": [{"id": "a", "element": "C", "hydrogens": 4}], "bonds": []}
        self.assertTrue(validate_graph(graph)["valid"])

    def test_disconnected_is_not_silently_reduced(self):
        result = parse_smiles("CC.O")
        self.assertFalse(result["valid"])
        self.assertEqual(len(result["graph"]["atoms"]), 3)

    def test_more_than_six_edits_in_one_transaction(self):
        source = parse_smiles("C1CCCCC1")["graph"]
        operations = []
        for bond in source["bonds"][::2]:
            operations.append({"op": "set_bond", "a": bond["a"], "b": bond["b"], "order": 2})
        operations.extend({"op": "set_atom", "id": atom["id"], "hydrogens": 1} for atom in source["atoms"])
        self.assertEqual(len(operations), 9)
        edited = apply_patch(source, operations)
        result = validate_graph(edited, {"C": 6})
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(result["canonical_smiles"], "c1ccccc1")
        self.assertEqual(validate_graph(source)["canonical_smiles"], "C1CCCCC1")

    def test_explicit_hydrogens_must_be_updated(self):
        source = parse_smiles("CC")["graph"]
        bond = source["bonds"][0]
        bad = apply_patch(source, [{"op": "set_bond", "a": bond["a"], "b": bond["b"], "order": 2}])
        self.assertFalse(validate_graph(bad)["valid"])
        changes = [{"op": "set_atom", "id": atom["id"], "hydrogens": 2} for atom in bad["atoms"]]
        good = apply_patch(bad, changes)
        self.assertEqual(validate_graph(good)["canonical_smiles"], "C=C")

    def test_transaction_failure_does_not_mutate_source(self):
        graph = parse_smiles("CC")["graph"]
        before = copy.deepcopy(graph)
        with self.assertRaises(ValueError):
            apply_patch(graph, [{"op": "set_atom", "id": graph["atoms"][0]["id"], "hydrogens": 2},
                                {"op": "remove_bond", "a": "unknown", "b": "also_unknown"}])
        self.assertEqual(graph, before)

    def test_cannot_add_atoms_or_change_elements(self):
        graph = parse_smiles("CC")["graph"]
        for operation in [{"op": "add_atom", "element": "O"},
                          {"op": "set_atom", "id": graph["atoms"][0]["id"], "element": "N"}]:
            with self.assertRaises(ValueError):
                apply_patch(graph, [operation])
        self.assertFalse(validate_graph(graph, {"C": 3})["valid"])

    def test_malformed_molecode_rejected(self):
        cases = ["graph TB\na[CH3]\na[CH3]", "graph TB\na[CH3]\na --- b",
                 "graph TB\na[CH3]\nignore this garbage", "graph TB\na[CH3]\na --- a",
                 "graph TB\na[CH3]\nb[CH3]\na --- b\nb --- a",
                 'graph TB\nsubgraph M["M"]\na[CH4]']
        for text in cases:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_graph(text)

    def test_chemically_invalid_graph_syntax_still_parseable(self):
        graph = parse_graph("graph TB\na[CH5]")
        self.assertFalse(validate_graph(graph)["valid"])

    def test_no_implicit_hydrogen_repair(self):
        graph = parse_graph("graph TB\na[C]\nb[C]\na --- b")
        result = validate_graph(graph)
        self.assertFalse(result["valid"])
        self.assertIsNone(result["canonical_smiles"])

    def test_isotopes_and_atom_maps_preserved(self):
        for smiles in ["[13CH3]CO", "[2H]OC", "[CH3:7][OH:9]"]:
            parsed = parse_smiles(smiles)
            self.assertTrue(parsed["valid"], parsed["errors"])
            self.assertEqual(Chem.MolToSmiles(Chem.MolFromSmiles(smiles)), parsed["canonical_smiles"])

    def test_unknown_isotope_mass_is_not_accepted(self):
        for smiles in ("[999CH4]", "[3CH4]"):
            with self.subTest(smiles=smiles):
                parsed = parse_smiles(smiles)
                self.assertIsNotNone(parsed["graph"])
                result = validate_graph(parsed["graph"])
                self.assertFalse(result["valid"])
                self.assertIsNone(result["canonical_smiles"])
                self.assertIsNone(result["exact_mass"])
                self.assertTrue(any("Unsupported isotope" in error for error in result["errors"]))
        for smiles in ("[13CH4]", "[2H]O[2H]"):
            with self.subTest(smiles=smiles):
                result = validate_graph(parse_smiles(smiles)["graph"])
                self.assertTrue(result["valid"], result["errors"])
                self.assertGreater(result["exact_mass"], 0)

    def test_radical_preserved_but_not_exported_as_parent(self):
        parsed = parse_smiles("[CH3]")
        self.assertEqual(parsed["graph"]["atoms"][0]["radicals"], 1)
        self.assertFalse(parsed["valid"])
        self.assertEqual(parse_graph(parsed["molecode"])["atoms"][0]["radicals"], 1)

    def test_complex_stereo_and_random_atom_order(self):
        for smiles in ["F/C(Cl)=C(/Br)I", "C/C(Cl)=C(/C)Br", "N[C@@H](C)C(=O)O"]:
            molecule = Chem.MolFromSmiles(smiles)
            expected = Chem.MolToSmiles(molecule, isomericSmiles=True)
            for _ in range(30):
                variant = Chem.MolToSmiles(molecule, canonical=False, doRandom=True, isomericSmiles=True)
                parsed = parse_smiles(variant)
                self.assertTrue(parsed["valid"], (variant, parsed["errors"]))
                self.assertEqual(parsed["canonical_smiles"], expected)

    def test_direct_absolute_cip_suffix_is_explicitly_rejected(self):
        template = "graph TB\nMol_C_1_{suffix}[CH]\nf[F]\ncl[Cl]\nbr[Br]\nMol_C_1_{suffix} --- f\nMol_C_1_{suffix} --- cl\nMol_C_1_{suffix} --- br"
        for suffix in ("R", "S"):
            text = template.format(suffix=suffix)
            with self.subTest(suffix=suffix), self.assertRaisesRegex(ValueError, "unsupported absolute CIP suffix"):
                parse_graph(text)
            # A relative RDKit tag does not prove consistency with absolute CIP.
            text += '\n%%@atom Mol_C_1_' + suffix + ' {"chiral_tag":1}'
            with self.assertRaisesRegex(ValueError, "unsupported absolute CIP suffix"):
                parse_graph(text)

    def test_direct_ez_requires_explicit_consistent_reference_metadata(self):
        template = "graph TB\nf[F]\na[CH]\nb[CH]\ncl[Cl]\nf --- a\na ===|{label}| b\nb --- cl"
        for label, stereo in (("E", 3), ("Z", 2)):
            with self.subTest(label=label):
                text = template.format(label=label)
                with self.assertRaisesRegex(ValueError, "stereo-reference metadata"):
                    parse_graph(text)
                with self.assertRaisesRegex(ValueError, "two references"):
                    parse_graph(text + '\n%%@bond a b {"stereo":' + str(stereo) + ',"stereo_atoms":[]}')
                graph = parse_graph(text + '\n%%@bond a b {"stereo":' + str(stereo) + ',"stereo_atoms":["f","cl"]}')
                result = validate_graph(graph)
                self.assertTrue(result["valid"], result["errors"])
                expected = Chem.MolToSmiles(Chem.MolFromSmiles("F/C=C/Cl" if label == "E" else "F/C=C\\Cl"))
                self.assertEqual(result["canonical_smiles"], expected)
                other = 2 if stereo == 3 else 3
                with self.assertRaisesRegex(ValueError, "stereo-reference metadata"):
                    parse_graph(text + '\n%%@bond a b {"stereo":' + str(other) + ',"stereo_atoms":["f","cl"]}')

    def test_stable_ids_across_rdkit_rebuild(self):
        graph = parse_smiles("CCO", name="Seed7")["graph"]
        rebuilt = graph_from_mol(mol_from_graph(graph), name="Other")
        self.assertEqual([a["id"] for a in graph["atoms"]], [a["id"] for a in rebuilt["atoms"]])

    def test_unknown_hydrogen_cannot_pass_final_gate(self):
        graph = parse_graph("graph TB\na[C;H=?]")
        self.assertIsNone(graph["atoms"][0]["hydrogens"])
        self.assertFalse(validate_graph(graph)["valid"])

    def test_reordering_bonds_does_not_invert_stereo(self):
        source = parse_smiles("N[C@@H](C)C(=O)O")
        graph = source["graph"]
        center = next(a["id"] for a in graph["atoms"] if a["chiral_tag"])
        edge = next(b for b in graph["bonds"] if center in (b["a"], b["b"]))
        operations = [{"op": "remove_bond", "a": edge["a"], "b": edge["b"]},
                      {"op": "set_bond", "a": edge["a"], "b": edge["b"], "order": edge["order"]}]
        edited = apply_patch(graph, operations)
        self.assertEqual(validate_graph(edited)["canonical_smiles"], source["canonical_smiles"])


if __name__ == "__main__":
    unittest.main()
