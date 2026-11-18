"""Formula accounting without RDKit's fragment hydrogen completion."""
from collections import Counter
from rdkit import Chem

ELECTRON_MASS = 0.000548579909065


def atom_counts(atoms):
    counts = Counter()
    for atom in atoms:
        isotope = int(atom.get("isotope", 0))
        counts[(atom["element"], isotope)] += 1
        h = atom.get("hydrogens")
        if h is None or not isinstance(h, int) or h < 0:
            raise ValueError("Fragment accounting requires resolved nonnegative hydrogen counts")
        counts[("H", 0)] += h
    return +counts


def formula(counts):
    # Hill ordering; isotope-labelled atoms remain distinct.
    keys = sorted(counts, key=lambda k: (0 if k[0] == "C" else 1 if k[0] == "H" else 2, k))
    return "".join((f"[{iso}{element}]" if iso else element) + (str(counts[(element, iso)]) if counts[(element, iso)] != 1 else "")
                   for element, iso in keys if counts[(element, iso)] > 0)


def masses(counts, charge=1):
    if not isinstance(charge, int) or isinstance(charge, bool):
        raise ValueError("Charge must be an integer")
    pt = Chem.GetPeriodicTable()
    exact, nominal = 0.0, 0
    for (element, isotope), count in counts.items():
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError("Atom counts must be nonnegative integers")
        z = pt.GetAtomicNumber(element)
        mass = pt.GetMassForIsotope(z, isotope) if isotope else pt.GetMostCommonIsotopeMass(z)
        mass_number = isotope or pt.GetMostCommonIsotope(z)
        if mass <= 0:
            raise ValueError(f"Unknown isotope: {isotope}{element}")
        exact += count * mass
        nominal += count * mass_number
    divisor = abs(charge) if charge else 1
    return {"exact_mz": (exact - charge * ELECTRON_MASS) / divisor,
            "nominal_mass": nominal / divisor if divisor != 1 else nominal}


def check_conservation(parent, ion, neutrals, ion_charge=1, neutral_charges=None):
    if any(not isinstance(n, int) or isinstance(n, bool) or n < 0 for c in [parent, ion, *neutrals] for n in c.values()):
        return False
    neutral_charges = [0] * len(neutrals) if neutral_charges is None else neutral_charges
    if len(neutral_charges) != len(neutrals):
        return False
    total = Counter(ion)
    for neutral in neutrals:
        total.update(neutral)
    return (+total == +Counter(parent)
            and ion_charge + sum(neutral_charges) == 1)


def electron_parity_ok(counts, charge, radicals):
    pt = Chem.GetPeriodicTable()
    electrons = sum(pt.GetAtomicNumber(e) * n for (e, _), n in counts.items()) - charge
    return radicals >= 0 and electrons % 2 == radicals % 2

