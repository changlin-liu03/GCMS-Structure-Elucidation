"""Unit-resolution comparison: nominal isotopic masses, not rounded exact mass."""


def match_nominal_mass(nominal_mass, peaks):
    return [int(p["mz"]) for p in peaks if int(p["mz"]) == nominal_mass]

