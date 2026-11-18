from typing import TypedDict


class NeutralLoss(TypedDict):
    formula: str
    atom_ids: list[str]
    charge: int
    radicals: int


# JSON uses the reserved Python word "from"; use functional TypedDict syntax.
BondChange = TypedDict("BondChange", {"a": str, "b": str, "from": float, "to": float})


class FragmentEvidence(TypedDict):
    rule_id: str
    rule_version: str
    atom_ids: list[str]
    broken_bonds: list[list[str]]
    hydrogen_changes: dict[str, int]
    bond_changes: list[BondChange]
    ion_formula: str
    charge: int
    radicals: int
    exact_mz: float
    nominal_mass: float
    neutral_losses: list[NeutralLoss]
    steps: int
    verified: bool
    verification: dict[str, bool]
    limitations: list[str]
    peak_mz: int


class EvidenceResult(TypedDict):
    evidence: list[FragmentEvidence]
    supported_peaks: list[int]
    diagnostics: dict
    complete: bool


class PeakContribution(TypedDict):
    mz: int
    weight: float
    supported: bool
    contribution: float


class SupportScore(TypedDict):
    intensity_basis: str
    score: float
    confidence_score: float
    score_lower: float
    score_upper: float
    supported_peaks: list[int]
    unassigned_peaks: list[int]
    score_semantics: str
    peak_contributions: list[PeakContribution]

