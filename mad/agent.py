from pathlib import Path
import re
import yaml

from mad.skill import Skill


MAD_ROOT = Path(__file__).resolve().parent.parent


class Agent:
    def __init__(
        self,
        name,
        description,
        template,
        model,
        mad_root=None,
        server=None,
    ):
        self.name = name
        self.description = description
        self.template = template
        self.model = model

        self.server = server
        self.message_hub = None

        self.mad_root = Path(
            mad_root if mad_root is not None else MAD_ROOT
        )

        self.instructions = ""
        self.skill_names = []
        self.skills = []

        self._load_template()
        self._load_skills()

    @classmethod
    def from_definition(
        cls,
        definition,
        mad_root=None,
    ):
        definition = definition.lstrip("-").strip()

        match = re.match(
            r"^(.*?)\s*\(([^:]+):(.+)\)\s*$",
            definition,
        )

        if not match:
            raise ValueError(
                f"Invalid agent definition: {definition}"
            )

        role_text, template, model = match.groups()

        if " who " in role_text:
            name, description = role_text.split(
                " who ",
                1,
            )

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
            self.mad_root
            / "mad_agents"
            / "core"
            / self.template,

            self.mad_root
            / "mad_agents"
            / "software"
            / self.template,
        ]

        for path in candidates:
            if path.exists():
                return path

        raise FileNotFoundError(
            f"Agent template not found: {self.template}"
        )

    def _load_template(self):
        template_path = self._find_template()

        text = template_path.read_text(
            encoding="utf-8"
        )

        metadata = {}
        body = text

        if text.startswith("---"):
            parts = text.split(
                "---",
                2,
            )

            if len(parts) == 3:
                metadata = (
                    yaml.safe_load(parts[1])
                    or {}
                )

                body = parts[2].strip()

        self.instructions = body

        self.skill_names = metadata.get(
            "skills",
            [],
        )

    def _load_skills(self):
        self.skills = [
            Skill.load(
                skill_name,
                mad_root=self.mad_root,
            )
            for skill_name in self.skill_names
        ]

    def build_system_prompt(
        self,
        project,
    ):
        parts = []

        #
        # Agent role instructions
        #

        parts.append(
            self.instructions
        )

        #
        # Project-specific role
        #

        if self.description:
            parts.append(
                "# Project Role\n\n"
                f"You are acting as **{self.name}** "
                f"{self.description}."
            )

        #
        # Skills
        #

        if self.skills:
            skill_text = []

            for skill in self.skills:
                skill_text.append(
                    f"## Skill: {skill.name}\n\n"
                    f"{skill.instructions}"
                )

            parts.append(
                "# Assigned Skills\n\n"
                + "\n\n".join(skill_text)
            )

        #
        # Project context
        #

        context = [
            "# Project",
            "",
            "## Objective",
            project.objective,
            "",
            "## Workspace",
            project.workspace,
        ]

        if project.success:
            context.extend(
                [
                    "",
                    "## Success Criteria",
                ]
            )

            for item in project.success:
                context.append(
                    f"- {item}"
                )

        parts.append(
            "\n".join(context)
        )

        #
        # Team roster
        #

        team = [
            "# Team",
            "",
            "These are the ONLY agents available in this project:",
            "",
        ]

        for agent in project.agents:
            line = f"- {agent.name}"

            if agent.description:
                line += f": {agent.description}"

            team.append(line)

        team.extend(
            [
                "",
                "When communicating with another agent:",
                "- Use the exact agent name listed above.",
                "- Never invent agent names or roles.",
                "- Do not refer to a generic 'Director' unless that is the exact name of an agent.",
                "- Do not message yourself unless there is a specific reason to do so.",
            ]
        )

        parts.append(
            "\n".join(team)
        )

        return "\n\n---\n\n".join(parts)

    def __repr__(self):
        return (
            f"Agent("
            f"name={self.name!r}, "
            f"template={self.template!r}, "
            f"model={self.model!r}"
            f")"
        )
