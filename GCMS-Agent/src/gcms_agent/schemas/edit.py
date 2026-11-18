from typing import Literal, TypedDict


class SetBond(TypedDict):
    op: Literal["set_bond"]
    a: str
    b: str
    order: float


class RemoveBond(TypedDict):
    op: Literal["remove_bond"]
    a: str
    b: str


class _SetAtomRequired(TypedDict):
    op: Literal["set_atom"]
    id: str


class SetAtom(_SetAtomRequired, total=False):
    hydrogens: int
    charge: int
    radicals: int
    aromatic: bool


EditOperation = SetBond | RemoveBond | SetAtom


class EditProposal(TypedDict):
    base_version: int
    operations: list[EditOperation]
    reason: str
    peak_refs: list[int]
    operation_explanations: list[str]


class Review(TypedDict):
    """Legacy review, also used internally after batch trial references expand."""

    decision: Literal["keep", "edit"]
    proposals: list[EditProposal]
    summary: str


class TrialProposal(TypedDict):
    """One candidate proposal passed to the batch analyze_proposals tool."""

    candidate_id: str
    base_version: int
    operations: list[EditOperation]


class TrialReference(TypedDict):
    """Reference to a validated trial; submission does not repeat operations."""

    trial_id: str
    reason: str
    peak_refs: list[int]
    operation_explanations: list[str]


ReviewCheck = Literal["graph_read", "valence_checked", "spectrum_checked", "alternatives_checked"]


class _CandidateReviewRequired(TypedDict):
    candidate_id: str
    decision: Literal["keep", "edit"]
    proposals: list[TrialReference]
    summary: str


class CandidateReview(_CandidateReviewRequired, total=False):
    """Model-facing batch item; the host independently decides completion."""

    checks: list[ReviewCheck]
    stop_review: bool  # Deprecated compatibility hint; new submissions omit it.


class BatchReview(TypedDict):
    reviews: list[CandidateReview]


class Acceptance(TypedDict, total=False):
    accepted: bool
    reason: str
    score_change: float | None
    improves_best: bool
    new_supported_peaks: list[int]

