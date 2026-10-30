from pathlib import Path


class Skill:
    def __init__(self, name, path, content):
        self.name = name
        self.path = Path(path)
        self.content = content

    @classmethod
    def load(cls, skill_name, mad_root):
        mad_root = Path(mad_root)

        skill_path = (
            mad_root
            / "mad_skills"
            / skill_name
            / "SKILL.md"
        )

        if not skill_path.exists():
            raise FileNotFoundError(
                f"Skill not found: {skill_name} ({skill_path})"
            )

        return cls(
            name=skill_name,
            path=skill_path,
            content=skill_path.read_text(encoding="utf-8"),
        )

    def __repr__(self):
        return f"Skill(name={self.name!r})"
