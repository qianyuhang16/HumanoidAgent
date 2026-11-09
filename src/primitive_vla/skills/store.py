from dataclasses import dataclass
from pathlib import Path
import json


@dataclass(frozen=True)
class Skill:
    id: str
    version: str
    description: str
    path: Path


class SkillStore:
    """Versioned Markdown indexed by a small JSON manifest, never compiled to a workflow."""

    def __init__(self):
        self._skills: dict[str, Skill] = {}

    def load(self, directory: str | Path) -> None:
        root = Path(directory).resolve()
        manifest = json.loads((root / "skills.json").read_text(encoding="utf-8"))
        for item in manifest:
            path = (root / item["path"]).resolve()
            if not path.is_relative_to(root) or not path.is_file() or item["id"] in self._skills:
                raise ValueError("skill path must exist under its package; IDs must be unique")
            self._skills[item["id"]] = Skill(item["id"], item["version"], item["description"], path)

    def catalog(self) -> list[dict[str, str]]:
        return [{"id": skill.id, "version": skill.version, "description": skill.description}
                for skill in self._skills.values()]

    def read(self, skill_id: str, offset: int = 0, limit: int = 8000) -> dict:
        if offset < 0 or not 1 <= limit <= 16000:
            raise ValueError("invalid skill page bounds")
        skill = self._skills[skill_id]
        body = skill.path.read_text(encoding="utf-8")
        return {"id": skill.id, "version": skill.version, "content": body[offset:offset + limit],
                "offset": offset, "total_chars": len(body)}
