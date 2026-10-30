from pathlib import Path


class Skill:
    def __init__(self, name, path, content):
        self.name = name
        self.path = Path(path)
        self.content = content

    @classmethod
    def load(cls, skill_name, root_dir="."):
        root_dir = Path(root_dir)

        skill_path = (
            root_dir
            / "mad_skills"
            / skill_name
            / "SKILL.md"
        )

        if not skill_path.exists():
            raise FileNotFoundError(
                f"Skill not found: {skill_name} "
                f"({skill_path})"
            )

        return cls(
            name=skill_name,
            path=skill_path,
            content=skill_path.read_text(encoding="utf-8"),
        )

    def __repr__(self):
        return f"Skill(name={self.name!r})"
        