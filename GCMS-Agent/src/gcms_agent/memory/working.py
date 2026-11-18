"""Bind an existing checkpoint store to one sample; never store evaluation labels."""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Protocol

FORBIDDEN_FIELDS = {"true_smiles", "ground_truth", "groundtruth", "target_smiles",
                    "labels", "api_key", "secret", "token"}


class CheckpointStore(Protocol):
    def save(self, key: str, state: dict) -> None: ...
    def load(self, key: str) -> dict | None: ...


def reject_labels(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in FORBIDDEN_FIELDS:
                raise ValueError(f"Forbidden memory field: {key}")
            reject_labels(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            reject_labels(child)


def safe_id(value: str, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise ValueError(f"{label} must be a safe ASCII identifier")
    return value


class WorkingMemory:
    """Use save/load(candidate_id, ...) without accepting a new sample at read time."""

    def __init__(self, checkpoints: CheckpointStore, sample_id: str):
        self.checkpoints = checkpoints
        self.sample_id = safe_id(sample_id, "sample_id")

    def _key(self, candidate_id: str) -> str:
        candidate = safe_id(candidate_id, "candidate_id")
        # Length prefix prevents collisions between IDs containing underscores.
        return f"memory_{len(self.sample_id)}_{self.sample_id}_{candidate}"

    def save(self, candidate_id: str, state: dict) -> None:
        if not isinstance(state, dict):
            raise ValueError("Working state must be an object")
        reject_labels(state)
        if state.get("sample_id", self.sample_id) != self.sample_id:
            raise ValueError("Working state belongs to a different sample")
        if state.get("candidate_id", candidate_id) != candidate_id:
            raise ValueError("Working state belongs to a different candidate")
        envelope = {"schema_version": 1, "sample_id": self.sample_id,
                    "candidate_id": safe_id(candidate_id, "candidate_id"), "state": deepcopy(state)}
        self.checkpoints.save(self._key(candidate_id), envelope)

    def load(self, candidate_id: str) -> dict | None:
        envelope = self.checkpoints.load(self._key(candidate_id))
        if envelope is None:
            return None
        if (not isinstance(envelope, dict) or envelope.get("schema_version") != 1
                or envelope.get("sample_id") != self.sample_id
                or envelope.get("candidate_id") != candidate_id
                or not isinstance(envelope.get("state"), dict)):
            raise ValueError("Checkpoint scope or memory schema mismatch")
        reject_labels(envelope["state"])
        if (envelope["state"].get("sample_id", self.sample_id) != self.sample_id
                or envelope["state"].get("candidate_id", candidate_id) != candidate_id):
            raise ValueError("Stored working state belongs to a different sample or candidate")
        return deepcopy(envelope["state"])

