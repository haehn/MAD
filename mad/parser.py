from pathlib import Path

from mad.project import Project


class ProjectParser:
    SECTION_NAMES = {
        "WORKSPACE",
        "SUCCESS",
        "AGENTS",
    }

    def __init__(self, path):
        self.path = Path(path)

    def parse(self):
        if not self.path.exists():
            raise FileNotFoundError(f"Project file not found: {self.path}")

        lines = self.path.read_text(encoding="utf-8").splitlines()

        objective = []
        workspace = []
        success = []
        agents = []

        # Everything before the first recognized section is the objective.
        section = "objective"

        for raw_line in lines:
            line = raw_line.strip()

            if not line:
                continue

            # Detect section headers such as WORKSPACE:
            if line.endswith(":"):
                candidate = line[:-1].upper()

                if candidate in self.SECTION_NAMES:
                    section = candidate.lower()
                    continue

            if section == "objective":
                objective.append(line)

            elif section == "workspace":
                workspace.append(line)

            elif section == "success":
                success.append(self._list_item(line))

            elif section == "agents":
                agents.append(self._list_item(line))

        project = Project(
            objective=" ".join(objective).strip(),
            workspace=" ".join(workspace).strip(),
            success=success,
            agents=agents,
        )

        self._validate(project)
        return project

    @staticmethod
    def _list_item(line):
        if line.startswith("-"):
            return line[1:].strip()

        return line

    @staticmethod
    def _validate(project):
        if not project.objective:
            raise ValueError("Project objective is required.")

        if not project.workspace:
            raise ValueError("WORKSPACE is required.")

        if not project.agents:
            raise ValueError("AGENTS is required.")

        # SUCCESS is intentionally optional.
