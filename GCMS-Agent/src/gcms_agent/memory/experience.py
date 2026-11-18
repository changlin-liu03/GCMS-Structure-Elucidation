"""Optional read-only retrieval of explicitly reviewed train/dev cases.

There is no method that turns a current model answer into a new trusted case.
"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from .working import reject_labels, safe_id


class ExperienceMemory:
    def __init__(self, cases: list[dict] | None = None, *, enabled: bool = False,
                 max_results: int = 3, max_case_chars: int = 12000):
        if type(enabled) is not bool or type(max_results) is not int or max_results <= 0:
            raise ValueError("Invalid experience-memory configuration")
        self.enabled = enabled
        self.max_results = max_results
        self._cases = []
        if not enabled:
            if cases:
                raise ValueError("Experience memory is disabled; cases were not loaded")
            return
        if not isinstance(cases, list):
            raise ValueError("Enabled experience memory requires an explicit case list")
        seen = set()
        for case in cases:
            if not isinstance(case, dict):
                raise ValueError("Each experience must be an object")
            required = {"case_id", "sample_id", "split", "verified", "verified_by", "source", "tags", "content"}
            optional = {"spectrum_hash", "structure_key"}
            if required - set(case) or set(case) - required - optional:
                raise ValueError("Malformed curated experience case")
            reject_labels(case)
            safe_id(case["case_id"], "case_id")
            safe_id(case["sample_id"], "sample_id")
            if case["case_id"] in seen:
                raise ValueError("Duplicate curated case ID")
            seen.add(case["case_id"])
            if case["split"] not in ("train", "dev"):
                raise ValueError("Only train/dev experiences are allowed; test cases are forbidden")
            if case["verified"] is not True or not isinstance(case["verified_by"], str) or not case["verified_by"].strip():
                raise ValueError("Experience must be explicitly reviewed and identify its reviewer")
            if not isinstance(case["source"], str) or not case["source"].strip():
                raise ValueError("Experience must have an auditable source")
            if not isinstance(case["tags"], list) or any(not isinstance(t, str) or not t for t in case["tags"]):
                raise ValueError("Experience tags must be nonempty strings")
            if not isinstance(case["content"], dict) or not case["content"]:
                raise ValueError("Experience content must be a nonempty object")
            for field in optional & set(case):
                if not isinstance(case[field], str) or not case[field]:
                    raise ValueError(f"Invalid {field}")
            if len(json.dumps(case, ensure_ascii=False, allow_nan=False)) > max_case_chars:
                raise ValueError("Experience exceeds the per-case context budget")
            self._cases.append(deepcopy(case))

    @classmethod
    def from_file(cls, path, *, enabled=False, **kwargs):
        if not enabled:
            # Disabled means no dataset read, even if a path was configured.
            return cls(enabled=False, **kwargs)
        cases = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(cases, enabled=True, **kwargs)

    def retrieve(self, *, current_sample_id: str, tags=(), current_spectrum_hash=None,
                 excluded_structure_keys=()) -> list[dict]:
        safe_id(current_sample_id, "current_sample_id")
        if not self.enabled:
            return []
        if not isinstance(tags, (list, tuple, set)) or any(not isinstance(t, str) for t in tags):
            raise ValueError("Query tags must be strings")
        query = set(tags)
        excluded = set(excluded_structure_keys)
        matches = []
        for case in self._cases:
            if case["sample_id"] == current_sample_id:
                continue
            if current_spectrum_hash and case.get("spectrum_hash") == current_spectrum_hash:
                continue
            if case.get("structure_key") in excluded:
                continue
            overlap = len(query & set(case["tags"]))
            if query and not overlap:
                continue
            matches.append((overlap, case["case_id"], case))
        matches.sort(key=lambda row: (-row[0], row[1]))
        return [deepcopy(row[2]) for row in matches[:self.max_results]]

