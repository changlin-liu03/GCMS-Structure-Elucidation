"""Immutable source snapshots loaded from an explicit version/hash allowlist."""
from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path


class KnowledgeMemory:
    """manifest maps reference_id -> path, version, sha256 and source.

    Paths are relative to root; an entry cannot escape it through '..' or a
    symlink. Contents are loaded once, so a run sees a frozen knowledge snapshot.
    """

    def __init__(self, root, manifest: dict, max_reference_chars: int = 20000):
        if not isinstance(manifest, dict) or max_reference_chars <= 0:
            raise ValueError("Expected a reference manifest and positive content limit")
        self.root = Path(root).resolve()
        self._entries = {}
        for reference_id, entry in manifest.items():
            if not isinstance(reference_id, str) or not reference_id or not isinstance(entry, dict):
                raise ValueError("Malformed reference entry")
            if set(entry) != {"path", "version", "sha256", "source"}:
                raise ValueError("A reference needs path, version, sha256 and source")
            if not all(isinstance(value, str) and value for value in entry.values()):
                raise ValueError("Reference metadata must be nonempty strings")
            relative = Path(entry["path"])
            if relative.is_absolute():
                raise ValueError("Reference path must be relative to the knowledge root")
            resolved = (self.root / relative).resolve(strict=True)
            if not resolved.is_relative_to(self.root) or not resolved.is_file():
                raise ValueError("Reference path escapes the knowledge root")
            raw = resolved.read_bytes()
            actual_hash = hashlib.sha256(raw).hexdigest()
            if actual_hash != entry["sha256"]:
                raise ValueError(f"Reference hash mismatch: {reference_id}")
            text = raw.decode("utf-8")
            if len(text) > max_reference_chars:
                raise ValueError(f"Reference exceeds content budget: {reference_id}")
            self._entries[reference_id] = {"reference_id": reference_id, "text": text,
                                           "version": entry["version"], "sha256": actual_hash,
                                           "source": entry["source"]}

    def read(self, reference_id: str) -> dict:
        if reference_id not in self._entries:
            raise ValueError("Reference is not in the frozen allowlist")
        return deepcopy(self._entries[reference_id])

    def manifest(self) -> dict:
        return {key: {name: row[name] for name in ("version", "sha256", "source")}
                for key, row in self._entries.items()}

