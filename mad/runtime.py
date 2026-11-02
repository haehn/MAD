import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import traceback

from .agents import Agent, Bus, Intelligence, ResponseTooLong
from . import ui


PROTOCOL = """
Respond with one JSON object:
{"to":"agent name","text":"instructions, question, or status"} sends a message and waits for new input.
Supervisor first writes a contract file using Python, then adds "contract":"contracts/work.md" to send it to one worker.
OUTPUT paths name future artifacts; do not put them in "files" before they exist.
Contract contents are included in the worker's message. Workers execute only their current contract and ask Supervisor about problems.
INPUT lists existing workspace paths (or is empty), TASK describes the work and constraints, OUTPUT lists artifact paths only.
OUTPUT contains no acceptance or verification criteria. Supervisor decides acceptance and issues an updated contract for corrections.
Add "files":["workspace-relative path"] to return artifacts or reference existing files.
Save all deliveries, including designs and review findings, as workspace files before sending their paths.
Recipients read the referenced files using Python; messages coordinate work, not replace file deliveries.
Add "wait":false to a message only to send more messages or continue independent work before waiting.
Workers may message only the Supervisor. The Supervisor may message any worker and relay information between workers.
{"python":"Python source code"} executes Python in the workspace and returns its output.
{"wait":true} waits for another message.
{"done":"final summary and verification"} finishes the project.
Use pathlib for files and subprocess for commands. Paths are relative to the workspace.
The .mad folder contains infrastructure logs, not deliverables; do not read or modify it.
Execute work rather than claiming it was done. Ask questions when needed and report actual results.
"""


def record(path, event, **data):
    if path is None:
        return
    entry = {"time": datetime.now(timezone.utc).isoformat(), "run_id": path.stem, "event": event, **data}
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(entry, ensure_ascii=False) + "\n")
    readable = ui.log_entry(entry)
    if readable:
        with path.with_suffix(".md").open("a", encoding="utf-8") as output:
            output.write(readable)


def read_project(path):
    sections = {"task": [], "workspace": [], "success": [], "agents": []}
    section = "task"
    for raw in Path(path).expanduser().read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        header = line[:-1].lower() if line.endswith(":") else None
        if header in sections:
            section = header
        else:
            sections[section].append(line)

    task = " ".join(sections["task"])
    if sections["success"]:
        task += "\nSuccess criteria:\n" + "\n".join(sections["success"])
    workspace = " ".join(sections["workspace"])
    roles, models = {}, {}
    templates = Path(__file__).resolve().parent.parent / "mad_agents"
    for line in sections["agents"]:
        entry = line.removeprefix("-").strip()
        label, template, model = entry, None, None
        if " (" in entry:
            label, _, settings = entry.rpartition(" (")
            template, separator, model = settings[:-1].partition(":")
            if not settings.endswith(")") or not separator or not template.strip() or not model.strip():
                raise ValueError(f"Invalid agent definition: {line}")
            template, model = template.strip(), model.strip()
        name, _, description = label.partition(" who ")
        name = name.strip()
        if not name or name in roles:
            raise ValueError(f"Agent names must be nonempty and unique: {name!r}")
        role = description.strip() or name
        if template:
            paths = [templates / folder / template for folder in ("core", "software")]
            source = next((item for item in paths if item.is_file()), None)
            if source is None:
                raise ValueError(f"Role template not found: {template}")
            role = source.read_text(encoding="utf-8")
            if role.startswith("---"):
                role = role.split("---", 2)[2]
            role = role.strip()
            if description.strip():
                role += "\n" + description.strip()
            models[name] = model
        roles[name] = role
    if not sections["task"] or not workspace or not roles:
        raise ValueError("Task, WORKSPACE, and AGENTS are required.")
    return task, roles, workspace, models


def workspace_path(workspace, name):
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Provide a workspace-relative path.")
    root = Path(workspace).resolve()
    relative = Path(name)
    path = (root / relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(root):
        raise ValueError("Paths must be inside the workspace and use relative paths.")
    if ".mad" in relative.parts or ".mad" in path.relative_to(root).parts:
        raise ValueError("Infrastructure logs in .mad are not contract inputs or deliverables.")
    return path


def read_contract(workspace, name):
    path = workspace_path(workspace, name)
    if not path.is_file():
        raise ValueError(f"Contract file does not exist: {name}. Use Python to create its INPUT/TASK/OUTPUT sections before sending it.")
    sections = {}
    section = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        heading = line[2:].upper() if line.startswith("# ") else ""
        if heading in {"INPUT", "TASK", "OUTPUT"}:
            if heading in sections:
                raise ValueError(f"Duplicate contract section: {heading}.")
            section = heading
            sections[section] = []
        elif line:
            if section is None:
                raise ValueError("Contracts start with # INPUT, followed by # TASK and # OUTPUT.")
            sections[section].append(raw)
    if list(sections) != ["INPUT", "TASK", "OUTPUT"]:
        raise ValueError("Contracts require # INPUT, # TASK, and # OUTPUT in that order.")
    contract = {"path": path.relative_to(Path(workspace).resolve()).as_posix(),
                "TASK": "\n".join(sections["TASK"]).strip()}
    if not contract["TASK"]:
        raise ValueError("Contract TASK must describe the work.")
    for section in ("INPUT", "OUTPUT"):
        contract[section] = []
        for raw in sections[section]:
            if not raw.strip().startswith("- "):
                raise ValueError(f"Contract {section} must list paths as '- relative/path'.")
            item = workspace_path(workspace, raw.strip()[2:].strip())
            if section == "INPUT" and not item.exists():
                raise ValueError(f"Contract input does not exist: {raw.strip()[2:]}.")
            contract[section].append(item.relative_to(Path(workspace).resolve()).as_posix())
    if not contract["OUTPUT"]:
        raise ValueError("Contract OUTPUT must list at least one artifact path.")
    return contract


async def work(agent, instructions, workspace, supervisor, finished, log_path=None):
    try:
        conversation = [{"role": "system", "content": instructions}]
        feedback = None
        truncations = 0
        current_contract = None
        while True:
            if feedback is None:
                message = await agent.messaging.receive(agent.name)
                record(log_path, "message_received", agent=agent.name, sender=message.sender, text=message.text)
                if message.contract is not None:
                    current_contract = message.contract
                feedback = f"{message.sender}: {message.text}"
            inbox = agent.messaging.inboxes[agent.name]
            while not inbox.empty():
                message = inbox.get_nowait()
                record(log_path, "message_received", agent=agent.name, sender=message.sender, text=message.text)
                if message.contract is not None:
                    current_contract = message.contract
                feedback += f"\n{message.sender}: {message.text}"
            conversation.append({"role": "user", "content": feedback})
            record(log_path, "model_request", agent=agent.name, model=agent.intelligence.model,
                   server=agent.intelligence.server, messages=conversation)
            ui.activity(agent.name)
            try:
                reply = await agent.intelligence.reply(conversation, json_mode=True)
            except ResponseTooLong as error:
                truncations += 1
                record(log_path, "action_failed", agent=agent.name, error_type=type(error).__name__,
                       error=str(error), traceback=traceback.format_exc())
                if truncations >= 3:
                    raise
                feedback = ("Your response was truncated and was not executed. Return a shorter, complete JSON action. "
                            "Split large work into smaller actions.")
                ui.error(f"{agent.name}: response truncated; retrying with a shorter action ({truncations}/2).")
                continue
            truncations = 0
            record(log_path, "model_response", agent=agent.name, text=reply)
            conversation.append({"role": "assistant", "content": reply})
            conversation[1:] = conversation[1:][-20:]
            try:
                action = json.loads(reply)
                if not isinstance(action, dict):
                    raise ValueError("Return one JSON object.")
                if {"to", "text"} <= set(action) <= {"to", "text", "wait", "files", "contract"}:
                    waiting = action.get("wait", True)
                    if not isinstance(waiting, bool):
                        raise ValueError("Message wait must be true or false.")
                    if action["to"] == agent.name:
                        raise ValueError("Do not message yourself.")
                    if agent.name != supervisor and action["to"] != supervisor:
                        raise ValueError(f"Send questions, findings, and reports to {supervisor}; workers cannot message each other.")
                    text = action["text"]
                    contract = None
                    if "contract" in action:
                        if agent.name != supervisor:
                            raise ValueError("Only the Supervisor can issue or update a contract.")
                        contract = read_contract(workspace, action["contract"])
                        text += (f"\n\nContract: {contract['path']}\n\n# INPUT\n"
                                 + "\n".join(f"- {name}" for name in contract["INPUT"])
                                 + f"\n\n# TASK\n{contract['TASK']}\n\n# OUTPUT\n"
                                 + "\n".join(f"- {name}" for name in contract["OUTPUT"]))
                    files = []
                    if "files" in action:
                        if not isinstance(action["files"], list) or not action["files"]:
                            raise ValueError("files must be a nonempty list of workspace-relative file paths.")
                        for name in action["files"]:
                            path = workspace_path(workspace, name)
                            if not path.is_file() or path.stat().st_size == 0:
                                raise ValueError(f"Handoff file is missing or empty: {name}. The files field is for existing files; omit future OUTPUT paths until the worker creates them.")
                            files.append(path.relative_to(Path(workspace).resolve()).as_posix())
                        text += "\n\nFiles:\n" + "\n".join(f"- {name}" for name in files)
                    await agent.messaging.send(agent.name, action["to"], text, contract=contract)
                    record(log_path, "message_sent", agent=agent.name, recipient=action["to"], text=text,
                           files=files, contract=contract)
                    ui.message(agent.name, action["to"], text)
                    if waiting:
                        record(log_path, "agent_waiting", agent=agent.name, reason="Message sent", recipient=action["to"])
                        feedback = None
                    else:
                        feedback = "Message delivered. Continue your current turn."
                elif set(action) == {"python"}:
                    if agent.name != supervisor and current_contract is None:
                        raise ValueError("No contract received. Ask the Supervisor for an INPUT/TASK/OUTPUT contract before executing work.")
                    record(log_path, "python_started", agent=agent.name, code=action["python"])
                    result = await asyncio.to_thread(
                        subprocess.run, [sys.executable, "-c", action["python"]],
                        cwd=workspace, capture_output=True, text=True, timeout=30,
                    )
                    record(log_path, "python_finished", agent=agent.name, returncode=result.returncode,
                           stdout=result.stdout, stderr=result.stderr)
                    ui.tool_result(agent.name, result.returncode, result.stdout, result.stderr)
                    feedback = json.dumps({"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
                elif action == {"wait": True}:
                    record(log_path, "agent_waiting", agent=agent.name)
                    feedback = None
                elif set(action) == {"done"}:
                    if agent.name != supervisor:
                        raise ValueError("Report to the Supervisor; only the Supervisor can finish.")
                    if not inbox.empty():
                        record(log_path, "finish_deferred", agent=agent.name, reason="Unread messages")
                        feedback = "New messages arrived. Consider them before finishing."
                        continue
                    record(log_path, "supervisor_finished", agent=agent.name, summary=action["done"])
                    finished.set_result(action["done"])
                    return
                else:
                    raise ValueError("Choose to/text, python, wait, or done.")
            except (ValueError, TypeError, KeyError, OSError, subprocess.TimeoutExpired) as error:
                record(log_path, "action_failed", agent=agent.name, error_type=type(error).__name__,
                       error=str(error), reply=reply)
                ui.error(f"{agent.name}: {error}")
                feedback = f"Action failed: {error}"
    except asyncio.CancelledError:
        record(log_path, "agent_stopped", agent=agent.name, reason="Cancelled")
        raise
    except Exception as error:
        response = getattr(error, "response", None)
        record(log_path, "agent_failed", agent=agent.name, error_type=type(error).__name__,
               error=str(error), response_body=response.text if response is not None else None,
               traceback=traceback.format_exc())
        raise


async def start(task, roles, workspace, server, model, models=None):
    workspace = Path(workspace).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    shared_instructions = Path(__file__).with_name("instructions.md").read_text(encoding="utf-8")
    contract_template = (Path(__file__).resolve().parent.parent / "mad_agents/CONTRACT.md").read_text(encoding="utf-8")
    messaging = Bus()
    models = models or {}
    agents = [Agent(name, Intelligence(server, models.get(name, model)), messaging) for name in roles]
    supervisor = agents[0].name
    finished = asyncio.get_running_loop().create_future()
    log_dir = workspace / ".mad" / "audit"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + ".jsonl")
    record(log_path, "run_started", task=task, roles=roles, workspace=str(workspace), server=server,
           models={agent.name: agent.intelligence.model for agent in agents})
    ui.start(task, workspace, agents, log_path=log_path.with_suffix(".md"))
    try:
        async with asyncio.timeout(240):
            async with asyncio.TaskGroup() as group:
                running = []
                for agent in agents:
                    messaging.register(agent.name)
                    objective = f"Task: {task}\nContract template:\n{contract_template}\n" if agent.name == supervisor else ""
                    instructions = (f"You are {agent.name}.\n{roles[agent.name]}\n" + objective
                                    + f"Team: {', '.join(roles)}. Supervisor: {supervisor}.\n"
                                    f"Only {supervisor} may finish, after reviewing workers' results.\n"
                                    + shared_instructions + "\n" + PROTOCOL)
                    running.append(group.create_task(work(agent, instructions, workspace, supervisor, finished, log_path)))
                await messaging.send("user", supervisor, task)
                record(log_path, "message_sent", agent="user", recipient=supervisor, text=task)
                summary = await finished
                for actor in running:
                    actor.cancel()
    except BaseException as error:
        event = "run_cancelled" if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt)) else "run_failed"
        record(log_path, event, error_type=type(error).__name__, error=str(error), traceback=traceback.format_exc())
        raise
    record(log_path, "run_finished", summary=summary)
    ui.finish(summary)
    return summary


def run(task=None, roles=None, workspace=None):
    parser = argparse.ArgumentParser(description="Agents collaborate on a task. The first role coordinates.")
    parser.add_argument("project", nargs="?", help="Project Markdown file")
    parser.add_argument("--server", default="http://localhost:8000/v1")
    parser.add_argument("--model", help="Override the model for all agents")
    parser.add_argument("--workspace", help="Override the project workspace")
    args = parser.parse_args()
    models = {}
    if args.project:
        try:
            task, roles, workspace, models = read_project(args.project)
        except (OSError, ValueError) as error:
            parser.error(str(error))
    if not task or not roles or not (args.workspace or workspace):
        parser.error("Provide a project Markdown file with a task, WORKSPACE, and AGENTS.")
    model = args.model or "Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8"
    asyncio.run(start(task, roles, args.workspace or workspace, args.server, model,
                      models={} if args.model else models))


if __name__ == "__main__":
    run()
