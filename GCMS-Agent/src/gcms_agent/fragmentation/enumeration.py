"""Public, deterministic evidence API."""
from .rule_engine import RULE_IDS, load_rule_policy, enumerate_paths
from .conservation import atom_counts, masses
from .matching import match_nominal_mass
from .isotopes import annotate_isotopes
from copy import deepcopy


def analyze_spectrum(graph, peaks, rules_path=None):
    from gcms_agent.chemistry.smiles_io import mol_from_graph
    from rdkit import Chem
    policy = load_rule_policy(rules_path)
    try:
        mol = mol_from_graph(graph, sanitize=True)
        Chem.SanitizeMol(mol)
        if Chem.GetFormalCharge(mol) != 0 or len(Chem.GetMolFrags(mol)) != 1 or any(a.GetNumRadicalElectrons() for a in mol.GetAtoms()):
            raise ValueError("Expected connected, neutral, closed-shell parent")
        # MoleCode may use Kekule bonds with cleared aromatic flags. Perceive
        # aromaticity from the validated molecule for template applicability,
        # without modifying the user's graph or atom IDs.
        perceived = deepcopy(graph)
        for item, atom in zip(perceived["atoms"], mol.GetAtoms()):
            item["aromatic"] = atom.GetIsAromatic()
        paths = enumerate_paths(perceived, policy)
        parent_mass = masses(atom_counts(graph["atoms"]))["nominal_mass"]
    except (ValueError, RuntimeError, KeyError, TypeError) as error:
        return {"evidence": [], "supported_peaks": [], "diagnostics": {"error": str(error)}, "complete": False}
    evidence = []
    for path in paths:
        # Only registered, enabled, verified formal paths can support a peak.
        # Unregistered hypotheses and explicitly non-scoring paths are excluded.
        if (path.get("rule_id") not in RULE_IDS or path["rule_id"] not in policy["enabled"]
                or path.get("verified") is not True or path.get("used_for_scoring", True) is not True):
            continue
        for mz in match_nominal_mass(path["nominal_mass"], peaks):
            evidence.append({**path, "peak_mz": mz, "rule_version": policy["version"]})
    return {"evidence": evidence, "supported_peaks": sorted({e["peak_mz"] for e in evidence}),
            "complete": True, "diagnostics": {"rule_version": policy["version"], "max_steps": policy["max_steps"],
                "rule_policy_sha256": policy["policy_sha256"], "enabled_rules": sorted(policy["enabled"]),
                "enumerated_paths": len(paths), "matched_paths": len(evidence), "truncated": False,
                "molecular_nominal_mass": parent_mass, "molecular_mass_peak_present": parent_mass in {p["mz"] for p in peaks},
                "isotopes": annotate_isotopes(graph, parent_mass, peaks),
                "limitations": ["Coverage is conditional on this finite rule set; no fragment abundance prediction.",
                    "Unrestricted ring double cleavage, arbitrary HCN-loss chains and unlisted rearrangements are not scored.",
                    "M-H and M-H2 channels are restricted to the ethane motif; arbitrary C-H losses are not scored.",
                    "Isotope annotations are not counted as independent cleavage evidence."]}}

