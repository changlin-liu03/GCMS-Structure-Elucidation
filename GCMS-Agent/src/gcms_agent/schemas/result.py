from typing import Literal, TypedDict
from .evidence import EvidenceResult, SupportScore
from .molecule import MolecularGraph, ValidationResult


class Usage(TypedDict):
    known: bool
    input_tokens: int
    cache_read_input_tokens: int
    output_tokens: int


class CandidateResult(TypedDict, total=False):
    candidate_id: str
    original_smiles: str
    final_smiles: str | None
    graph: MolecularGraph
    initial_graph: MolecularGraph
    graph_version: int
    composition: dict[str, int]
    validation: ValidationResult
    initial_validation: ValidationResult
    evidence: EvidenceResult
    score: SupportScore | None
    initial_score: SupportScore | None
    result_status: Literal["kept", "corrected", "invalid_unresolved"]
    stop_reason: str
    spectral_status: str
    finished: bool
    rounds_completed: int
    stagnation_count: int
    accepted_edits: int
    format_errors: int
    elapsed_seconds: float
    history: list[dict]
    seen: list[str]
    rank: int | None
    tied: bool


class RankedStructure(TypedDict, total=False):
    final_smiles: str
    score: SupportScore
    rank: int
    tied: bool
    source_candidate_ids: list[str]
    source_candidate_count: int
    independent_candidate_count: int
    evidence_profile: dict
    identity_confirmed: Literal[False]
    selection_basis: Literal[
        "highest_rule_support_score",
        "most_source_candidates_among_top_score",
        "canonical_display_representative_of_count_tie",
    ]


class RankingResult(TypedDict):
    ranked_candidates: list[CandidateResult]
    unique_ranking: list[RankedStructure]
    unranked_candidates: list[CandidateResult]
    top1: RankedStructure | None
    top1_candidates: list[RankedStructure]
    ranking_diagnostics: dict
    status: str
    ranking_complete: bool

