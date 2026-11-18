from typing import TypedDict


class Atom(TypedDict):
    id: str
    element: str
    isotope: int
    charge: int
    hydrogens: int | None
    radicals: int
    aromatic: bool
    chiral_tag: int
    atom_map: int


class Bond(TypedDict):
    a: str
    b: str
    order: float
    stereo: int
    stereo_atoms: list[str]


class MolecularGraph(TypedDict):
    atoms: list[Atom]
    bonds: list[Bond]


class _ParseResultRequired(TypedDict):
    graph: MolecularGraph | None
    molecode: str | None
    valid: bool
    errors: list[str]
    warnings: list[str]
    canonical_smiles: str | None


class ParseResult(_ParseResultRequired, total=False):
    syntax_cleanup: list[str]


class ValidationResult(TypedDict, total=False):
    valid: bool
    errors: list[str]
    canonical_smiles: str | None
    formula: str | None
    exact_mass: float | None
    nominal_mass: int | None
    graph: MolecularGraph
    molecode: str | None
    composition: dict[str, int]
    hydrogen_count: int
    formal_charge: int
    dbe: float | None
    heavy_atom_count: int
    ring_count: int

