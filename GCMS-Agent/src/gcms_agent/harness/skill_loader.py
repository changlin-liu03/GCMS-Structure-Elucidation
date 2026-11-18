"""Versioned host-managed skills; no automatic user/project skill discovery."""

import hashlib
from pathlib import Path


SKILL_NAMES = (
    "gcms-input-audit", "molecode-graph-reasoning", "ei-fragment-evidence",
    "constrained-structure-edit", "rerank-and-explain",
)


class SkillLibrary:
    def __init__(self, project_root: Path):
        self.root = (Path(project_root) / "skills").resolve()
        self.references = {}
        self.skills = {}
        for name in SKILL_NAMES:
            directory = self.root / name
            path = directory / "SKILL.md"
            self.skills[name] = self._read(path)
            for folder in ("references", "examples"):
                for reference in sorted((directory / folder).glob("*.md")):
                    reference_id = reference.relative_to(self.root).as_posix()
                    self.references[reference_id] = self._read(reference)

    def _read(self, path: Path) -> dict:
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(self.root):
            raise ValueError("Skill path escapes the approved library")
        content = resolved.read_text(encoding="utf-8")
        return {"text": content, "sha256": hashlib.sha256(content.encode()).hexdigest()}

    def prompt(self) -> str:
        blocks = [f"<skill name='{name}' sha256='{item['sha256']}'>\n{item['text']}\n</skill>"
                  for name, item in self.skills.items()]
        return "\n\n".join(blocks)

    def read_reference(self, reference_id: str) -> dict:
        if reference_id not in self.references:
            raise ValueError("Unknown reference_id; use only the published reference list")
        return {"reference_id": reference_id, **self.references[reference_id]}

    def manifest(self) -> dict:
        return {
            "skills": {name: entry["sha256"] for name, entry in self.skills.items()},
            "references": {name: entry["sha256"] for name, entry in self.references.items()},
        }
