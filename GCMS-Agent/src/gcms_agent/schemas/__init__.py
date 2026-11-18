"""Shared type annotations for the existing JSON-dict interfaces.

These TypedDicts document wire objects; they do not replace runtime validation.
"""

from .spectrum import InferenceInput, Peak
from .molecule import MolecularGraph, ValidationResult
from .edit import (EditProposal, EditOperation, Review, TrialProposal, TrialReference,
                   ReviewCheck, CandidateReview, BatchReview)
from .evidence import EvidenceResult, FragmentEvidence, SupportScore
from .result import CandidateResult, RankedStructure, RankingResult

__all__ = ["InferenceInput", "Peak", "MolecularGraph", "ValidationResult",
           "EditProposal", "EditOperation", "Review", "EvidenceResult",
           "FragmentEvidence", "SupportScore", "CandidateResult", "RankedStructure", "RankingResult",
           "TrialProposal", "TrialReference", "ReviewCheck", "CandidateReview", "BatchReview"]

