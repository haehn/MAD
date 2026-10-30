from pathlib import Path

import yaml


class Skill:
    def __init__(self, name, path, instructions, metadata=None):
        self.name = name
        self.path = Path(path)
        self.instructions = instructions
        self.metadata = metadata or {}

    @classmethod
    def load(cls, skill_name, mad_root):
        skill_path = (
            Path(mad_root)
            / "mad_skills"
            / skill_name
            / "SKILL.md"
        )

        if not skill_path.exists():
            raise FileNotFoundError(
                f"Skill not found: {skill_name} ({skill_path})"
            )

        text = skill_path.read_text(encoding="utf-8")

        metadata = {}
        instructions = text

        if text.startswith("---"):
            parts = text.split("---", 2)

            if len(parts) == 3:
                metadata = yaml.safe_load(parts[1]) or {}
                instructions = parts[2].strip()

        return cls(
            name=skill_name,
            path=skill_path,
            instructions=instructions,
            metadata=metadata,
        )
