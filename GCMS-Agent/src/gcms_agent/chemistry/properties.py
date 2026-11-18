"""Measured/calculated molecular properties; not identification confidence."""
from rdkit import Chem
from rdkit.Chem import rdMolDescriptors


def molecular_properties(mol: Chem.Mol) -> dict:
    counts = {}
    for atom in mol.GetAtoms():
        element = atom.GetSymbol()
        counts[element] = counts.get(element, 0) + 1
        counts["H"] = counts.get("H", 0) + atom.GetTotalNumHs()
    exact_mass = rdMolDescriptors.CalcExactMolWt(mol)
    # Nominal mass is isotope mass-number sum, not rounded exact mass.
    table = Chem.GetPeriodicTable()
    nominal_mass = sum((a.GetIsotope() or table.GetMostCommonIsotope(a.GetAtomicNum()))
                       + a.GetTotalNumHs() for a in mol.GetAtoms())
    halogens = sum(counts.get(e, 0) for e in ("F", "Cl", "Br", "I"))
    # Classical DBE formula is meaningful for the CHNOS/halogen subset only.
    dbe = (1 + counts.get("C", 0) - (counts.get("H", 0) + halogens) / 2
           + counts.get("N", 0) / 2) if set(counts) <= {"C", "H", "N", "O", "S", "F", "Cl", "Br", "I"} else None
    return {"formula": rdMolDescriptors.CalcMolFormula(mol), "exact_mass": exact_mass,
            "nominal_mass": int(nominal_mass), "hydrogen_count": counts.get("H", 0),
            "formal_charge": Chem.GetFormalCharge(mol), "dbe": dbe,
            "heavy_atom_count": mol.GetNumHeavyAtoms(), "ring_count": rdMolDescriptors.CalcNumRings(mol)}

