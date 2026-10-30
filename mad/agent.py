import re
from pathlib import Path

MAD_ROOT = Path(__file__).resolve().parent.parent

import yaml

from mad.skill import Skill


class Agent:
    def __init__(
        self,
        name,
        description,
        template,
        model,
        mad_root=MAD_ROOT,
    ):
        self.name = name
        self.description = description
        self.template = template
        self.model = model
        self.mad_root = Path(
            mad_root if mad_root is not None else MAD_ROOT
        )

        self.instructions = ""
        self.skill_names = []
        self.skills = []

        self._load_template()
        self._load_skills()

    @classmethod
    def from_definition(cls, definition, mad_root=None):
        definition = definition.lstrip("-").strip()

        match = re.match(
            r"^(.*?)\s*\(([^:]+):(.+)\)\s*$",
            definition
        )

        if not match:
            raise ValueError(
                f"Invalid agent definition: {definition}"
            )

        role_text, template, model = match.groups()

        if " who " in role_text:
            name, description = role_text.split(" who ", 1)
            description = "who " + description
        else:
            name = role_text
            description = ""

        return cls(
            name=name.strip(),
            description=description.strip(),
            template=template.strip(),
            model=model.strip(),
            mad_root=mad_root,
        )

    def _find_template(self):
        candidates = [
            self.mad_root / "mad_agents" / "core" / self.template,
            self.mad_root / "mad_agents" / "software" / self.template,
        ]

        for path in candidates:
            if path.exists():
                return path

        raise FileNotFoundError(
            f"Agent template not found: {self.template}"
        )

    def _load_template(self):
        template_path = self._find_template()

        text = template_path.read_text(encoding="utf-8")

        metadata = {}
        body = text

        if text.startswith("---"):
            parts = text.split("---", 2)

            if len(parts) == 3:
                metadata = yaml.safe_load(parts[1]) or {}
                body = parts[2].strip()

        self.instructions = body
        self.skill_names = metadata.get("skills", [])

    def _load_skills(self):
        self.skills = [
            Skill.load(
                skill_name,
                mad_root=self.mad_root,
            )
            for skill_name in self.skill_names
        ]

    def __repr__(self):
        return (
            f"Agent("
            f"name={self.name!r}, "
            f"template={self.template!r}, "
            f"model={self.model!r}, "
            f"skills={self.skill_names!r}"
            f")"
        )
