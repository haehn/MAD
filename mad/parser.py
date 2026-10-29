from pathlib import Path


def parse_project(path):
    text = Path(path).read_text()

    project = {
        "task": "",
        "repository": "",
        "agents": [],
    }

    section = None

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if not line:
            continue

        if line.startswith("TASK:"):
            section = "task"
            project["task"] = line[len("TASK:"):].strip()
            continue

        if line.startswith("REPOSITORY:"):
            section = "repository"
            project["repository"] = line[len("REPOSITORY:"):].strip()
            continue

        if line.startswith("AGENTS:"):
            section = "agents"
            continue

        if section == "task":
            project["task"] += " " + line

        elif section == "repository":
            project["repository"] += " " + line

        elif section == "agents" and line.startswith("-"):
            project["agents"].append(line[1:].strip())

    return project
    