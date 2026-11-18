"""Permissive input parsing and loss-aware RDKit graph conversion.

Hydrogens in the graph are explicit counts: no implicit hydrogen completion is
allowed when rebuilding a molecule. Raw input counts are provisional until the
final chemical validator succeeds.
"""
from __future__ import annotations

import re
from collections import Counter

from rdkit import Chem, rdBase

from .graph_checks import require_graph

BOND_TYPES = {1: Chem.BondType.SINGLE, 1.5: Chem.BondType.AROMATIC,
              2: Chem.BondType.DOUBLE, 3: Chem.BondType.TRIPLE}


def clean_smiles_text(text: str) -> tuple[str, list[str]]:
    """Remove only unambiguous transport wrappers, never invent topology.

    In particular an unmatched ring or bracket is not guessed. Internal
    whitespace, names after SMILES, and CXSMILES annotations are unsupported,
    since RDKit's default name parsing can silently discard those suffixes.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("SMILES must be a nonempty string")
    cleaned = text.strip()
    actions = ["trim_outer_whitespace"] if cleaned != text else []
    if cleaned.startswith("```"):
        match = re.fullmatch(r"```(?:smiles)?[ \t]*\r?\n([^\r\n]+)\r?\n```", cleaned, flags=re.IGNORECASE)
        if not match:
            raise ValueError("Only a complete single-SMILES code fence can be removed")
        cleaned = match.group(1).strip()
        actions.append("remove_complete_smiles_code_fence")
    if not cleaned or re.search(r"\s", cleaned):
        raise ValueError("Internal whitespace, SMILES names, and multiple records are not accepted")
    if "|" in cleaned:
        raise ValueError("CXSMILES extension is unsupported; it cannot be silently dropped")
    return cleaned, actions


def graph_from_mol(mol: Chem.Mol, name: str = "Mol") -> dict:
    if mol is None:
        raise ValueError("Cannot serialize a missing molecule")
    prefix = re.sub(r"[^A-Za-z0-9]", "", name) or "Mol"
    if not prefix[0].isalpha():
        prefix = "Mol" + prefix
    counts = Counter()
    atoms, ids = [], []
    for atom in mol.GetAtoms():
        element = atom.GetSymbol()
        counts[element] += 1
        atom_id = atom.GetProp("_gcms_atom_id") if atom.HasProp("_gcms_atom_id") else f"{prefix}_{element}_{counts[element]}"
        ids.append(atom_id)
        try:
            hydrogen = int(atom.GetTotalNumHs())
        except RuntimeError:
            hydrogen = None
        atoms.append({"id": atom_id, "element": element, "isotope": atom.GetIsotope(),
                      "charge": atom.GetFormalCharge(), "hydrogens": hydrogen,
                      "radicals": atom.GetNumRadicalElectrons(), "aromatic": atom.GetIsAromatic(),
                      "chiral_tag": int(atom.GetChiralTag()), "atom_map": atom.GetAtomMapNum()})
    bonds = []
    for bond in mol.GetBonds():
        order = bond.GetBondTypeAsDouble()
        if order not in BOND_TYPES:
            raise ValueError(f"Unsupported bond type: {bond.GetBondType()}")
        bonds.append({"a": ids[bond.GetBeginAtomIdx()], "b": ids[bond.GetEndAtomIdx()],
                      "order": order, "stereo": int(bond.GetStereo()),
                      "stereo_atoms": [ids[i] for i in bond.GetStereoAtoms()]})
    graph = {"atoms": atoms, "bonds": bonds}
    require_graph(graph)
    return graph


def mol_from_graph(graph: dict, sanitize: bool = True) -> Chem.Mol:
    require_graph(graph)
    mol = Chem.RWMol()
    indices = {}
    for item in graph["atoms"]:
        if item.get("hydrogens") is None:
            raise ValueError(f"Unresolved hydrogen count at {item['id']}")
        atom = Chem.Atom(item["element"])
        atom.SetIsotope(item.get("isotope", 0))
        atom.SetFormalCharge(item.get("charge", 0))
        atom.SetNumExplicitHs(item["hydrogens"])
        atom.SetNoImplicit(True)
        atom.SetNumRadicalElectrons(item.get("radicals", 0))
        atom.SetIsAromatic(item.get("aromatic", False))
        atom.SetChiralTag(Chem.ChiralType.values[item.get("chiral_tag", 0)])
        atom.SetAtomMapNum(item.get("atom_map", 0))
        atom.SetProp("_gcms_atom_id", item["id"])
        indices[item["id"]] = mol.AddAtom(atom)
    for item in graph["bonds"]:
        mol.AddBond(indices[item["a"]], indices[item["b"]], BOND_TYPES[item["order"]])
    result = mol.GetMol()
    for item in graph["bonds"]:
        bond = result.GetBondBetweenAtoms(indices[item["a"]], indices[item["b"]])
        refs = item.get("stereo_atoms", [])
        if refs:
            if len(refs) != 2:
                raise ValueError("Bond stereo needs exactly two reference atoms")
            bond.SetStereoAtoms(indices[refs[0]], indices[refs[1]])
        bond.SetStereo(Chem.BondStereo.values[item.get("stereo", 0)])
    if sanitize:
        Chem.SanitizeMol(result)
        Chem.AssignStereochemistry(result, cleanIt=False, force=True)
        Chem.SetDoubleBondNeighborDirections(result)
    else:
        result.UpdatePropertyCache(strict=False)
    return result


def parse_smiles(smiles: str, name: str = "Mol") -> dict:
    from .molecode_adapter import render_graph
    from .validation import validate_graph

    result = {"graph": None, "molecode": None, "valid": False,
              "errors": [], "canonical_smiles": None, "warnings": []}
    try:
        cleaned, actions = clean_smiles_text(smiles)
        result["syntax_cleanup"] = actions
    except ValueError as exc:
        result["errors"] = [str(exc)]
        return result
    # Block RDKit diagnostics here; structured errors are returned to the caller.
    with rdBase.BlockLogs():
        parameters = Chem.SmilesParserParams()
        parameters.parseName = False
        parameters.allowCXSMILES = False
        strict = Chem.MolFromSmiles(cleaned, parameters)
        parameters.sanitize = False
        raw = strict if strict is not None else Chem.MolFromSmiles(cleaned, parameters)
    if raw is None:
        result["errors"] = ["SMILES syntax could not be parsed; no complete graph available"]
        return result
    try:
        raw.UpdatePropertyCache(strict=False)
        if strict is not None:
            raw = Chem.Mol(raw)
            # Readable conventional MoleCode, with deterministic aromatic fallback.
            try:
                Chem.Kekulize(raw, clearAromaticFlags=True)
            except Exception:
                raw = Chem.Mol(strict)
        graph = graph_from_mol(raw, name)
        result.update(graph=graph, molecode=render_graph(graph))
        checked = validate_graph(graph)
        result.update(valid=checked["valid"], errors=checked["errors"],
                      canonical_smiles=checked["canonical_smiles"])
        if strict is None:
            result["warnings"].append("Raw graph: hydrogen counts and aromatic assignments are provisional until strict validation passes")
    except Exception as exc:
        result["errors"] = [f"Graph conversion failed: {exc}"]
    return result


def repair_smiles_syntax(smiles: str, name: str = "Mol") -> dict:
    """Safe syntactic rescue entrypoint; successful cleanup still validates chemistry.

    Returns the same contract as parse_smiles, plus syntax_cleanup actions.
    Chemistry errors proceed to MoleCode repair; unrecoverable syntax retains
    graph=None and canonical_smiles=None.
    """
    return parse_smiles(smiles, name=name)

