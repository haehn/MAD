import asyncio
import json
import re
import time

from mad.message import Message
from mad.ui import console


#
# Task planning
#

TASK_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "owner": {"type": "string"},
        "dependencies": {
            "type": "array",
            "items": {"type": "string"},
        },
        "acceptance": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "id",
        "title",
        "description",
        "owner",
        "dependencies",
        "acceptance",
    ],
    "additionalProperties": False,
}


TASK_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "items": TASK_SCHEMA,
        },
    },
    "required": ["tasks"],
    "additionalProperties": False,
}


#
# Agent communication
#

MESSAGE_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "recipient": {"type": "string"},
        "type": {
            "type": "string",
            "enum": [
                "question",
                "answer",
                "status",
                "handoff",
                "review",
                "blocker",
                "discovery",
            ],
        },
        "content": {"type": "string"},
    },
    "required": [
        "recipient",
        "type",
        "content",
    ],
    "additionalProperties": False,
}


MESSAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "messages": {
            "type": "array",
            "items": MESSAGE_ITEM_SCHEMA,
        },
    },
    "required": ["messages"],
    "additionalProperties": False,
}


#
# Workspace tools
#
# Only "tool" is mandatory.
#
# Examples:
#
# {"tool": "list", "path": "."}
#
# {"tool": "read", "path": "src/main.js"}
#
# {"tool": "write", "path": "index.html", "content": "..."}
#
# {"tool": "run", "command": "npm test"}
#
# {"tool": "serve", "command": "npm start", "name": "dev"}
#

TOOL_CALL_SCHEMA = {
    "type": "object",
    "properties": {
        "tool": {
            "type": "string",
            "enum": [
                "list",
                "read",
                "write",
                "append",
                "run",
                "serve",
                "stop_server",
            ],
        },
        "path": {
            "type": "string",
        },
        "content": {
            "type": "string",
        },
        "command": {
            "type": "string",
        },
        "name": {
            "type": "string",
        },
    },
    "required": [
        "tool",
    ],
    "additionalProperties": False,
}


#
# Tool and final responses are separate.
#

TASK_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "state": {
            "type": "string",
            "enum": [
                "tool",
                "final",
            ],
        },
    },
    "required": [
        "state",
    ],
    "additionalProperties": False,
}


TOOL_STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "tool_calls": {
            "type": "array",
            "items": TOOL_CALL_SCHEMA,
        },
        "messages": {
            "type": "array",
            "items": MESSAGE_ITEM_SCHEMA,
        },
    },
    "required": [
        "tool_calls",
        "messages",
    ],
    "additionalProperties": False,
}


FINAL_STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": [
                "completed",
                "blocked",
            ],
        },
        "summary": {
            "type": "string",
        },
        "output": {
            "type": "string",
        },
        "messages": {
            "type": "array",
            "items": MESSAGE_ITEM_SCHEMA,
        },
    },
    "required": [
        "status",
        "summary",
        "output",
        "messages",
    ],
    "additionalProperties": False,
}


#
# Replanning
#

REPLAN_ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "enum": [
                "add",
                "revise",
                "reassign",
                "cancel",
                "reopen",
            ],
        },
        "task_id": {
            "type": "string",
        },
        "owner": {
            "type": "string",
        },
        "task": TASK_SCHEMA,
        "updates": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                },
                "description": {
                    "type": "string",
                },
                "owner": {
                    "type": "string",
                },
                "dependencies": {
                    "type": "array",
                    "items": {
                        "type": "string",
                    },
                },
                "acceptance": {
                    "type": "array",
                    "items": {
                        "type": "string",
                    },
                },
            },
            "additionalProperties": False,
        },
    },
    "required": [
        "type",
    ],
    "additionalProperties": False,
}


REPLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {
            "type": "string",
        },
        "actions": {
            "type": "array",
            "items": REPLAN_ACTION_SCHEMA,
        },
    },
    "required": [
        "reason",
        "actions",
    ],
    "additionalProperties": False,
}


def parse_json_response(response):
    response = response.strip()

    response = re.sub(
        r"^```(?:json)?\s*",
        "",
        response,
        flags=re.IGNORECASE,
    )

    response = re.sub(
        r"\s*```$",
        "",
        response,
    )

    return json.loads(
        response
    )


class AgentRuntime:
    def __init__(
        self,
        agent,
        project,
        hub,
        workspace,
    ):
        self.agent = agent
        self.project = project
        self.hub = hub
        self.workspace = workspace

        self.running = True

    async def run(self):
        while self.running:
            message = await self.hub.receive(
                self.agent.name
            )

            if message.type == "STOP":
                self.running = False
                break

            try:
                if message.type == "PLAN_REQUEST":
                    await self._handle_plan_request(
                        message
                    )

                elif message.type == "REPLAN_REQUEST":
                    await self._handle_replan_request(
                        message
                    )

                elif message.type == "TASK":
                    await self._handle_task(
                        message
                    )

                elif message.type == "PEER":
                    await self._handle_peer_message(
                        message
                    )

            except Exception as error:
                console.print(
                    f"[bold red]"
                    f"Agent {self.agent.name} crashed:"
                    f"[/bold red] "
                    f"{error}"
                )

    async def _chat(
        self,
        messages,
        max_tokens=4096,
        json_schema=None,
    ):
        return await asyncio.to_thread(
            self.agent.server.chat,
            model=self.agent.model,
            messages=messages,
            max_tokens=max_tokens,
            json_schema=json_schema,
        )

    async def _structured_chat(
        self,
        messages,
        schema,
        max_tokens=4096,
        retries=2,
    ):
        conversation = list(
            messages
        )

        for attempt in range(
            retries + 1
        ):
            response = await self._chat(
                conversation,
                max_tokens=max_tokens,
                json_schema=schema,
            )

            try:
                return (
                    parse_json_response(
                        response
                    ),
                    response,
                )

            except json.JSONDecodeError as error:
                console.print(
                    f"[yellow]"
                    f"⚠ {self.agent.name} returned "
                    f"invalid JSON "
                    f"(attempt {attempt + 1}/"
                    f"{retries + 1}): "
                    f"{error}"
                    f"[/yellow]"
                )

                console.print(
                    f"[dim]"
                    f"Raw response: "
                    f"{response[:1000]}"
                    f"[/dim]"
                )

                if attempt >= retries:
                    raise

                conversation.append(
                    {
                        "role": "assistant",
                        "content": response,
                    }
                )

                conversation.append(
                    {
                        "role": "user",
                        "content": (
                            "Your previous response was invalid "
                            "or incomplete JSON. Return one complete "
                            "valid JSON object matching the schema."
                        ),
                    }
                )

        raise RuntimeError(
            "Structured response failed."
        )

    #
    # Planning
    #

    async def _handle_plan_request(
        self,
        message,
    ):
        start_time = time.perf_counter()

        console.print(
            f"[bright_magenta]▶[/bright_magenta] "
            f"[bold]{self.agent.name}[/bold] "
            f"[dim]creating initial task graph...[/dim]"
        )

        agent_list = "\n".join(
            f"- {name}"
            for name
            in message.metadata["agents"]
        )

        prompt = f"""
Create an initial task graph for the project.

Available agents:

{agent_list}

Use exact agent names.

Create tasks with:
- unique IDs
- clear titles
- useful descriptions
- exact owners
- dependencies
- acceptance criteria

Use the available agents according to their roles and skills.

Keep the plan proportional to the project.
Avoid unnecessary work.

The task graph may evolve as work progresses.
""".strip()

        result, _ = await self._structured_chat(
            [
                {
                    "role": "system",
                    "content": (
                        self.agent.build_system_prompt(
                            self.project
                        )
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            schema=TASK_PLAN_SCHEMA,
            max_tokens=8192,
        )

        elapsed = (
            time.perf_counter()
            - start_time
        )

        console.print(
            f"[bright_magenta]✓[/bright_magenta] "
            f"[bold]{self.agent.name}[/bold] "
            f"[dim]created task graph "
            f"in {elapsed:.1f}s[/dim]"
        )

        await self.hub.send(
            Message(
                sender=self.agent.name,
                recipient="__runtime__",
                type="PLAN_RESULT",
                content=json.dumps(
                    result
                ),
                metadata={
                    "elapsed": elapsed,
                },
            )
        )

    #
    # Replanning
    #

    async def _handle_replan_request(
        self,
        message,
    ):
        start_time = time.perf_counter()

        current_tasks = [
            task.to_dict()
            for task
            in self.project.tasks
        ]

        event_type = message.metadata.get(
            "event_type",
            "result",
        )

        console.print(
            f"[bright_magenta]◆[/bright_magenta] "
            f"[bold]{self.agent.name}[/bold] "
            f"[dim]reviewing {event_type} "
            f"from {message.sender}[/dim]"
        )

        agent_list = "\n".join(
            f"- {agent.name}"
            for agent
            in self.project.agents
        )

        prompt = f"""
Review the current project plan.

Event type:
{event_type}

From:
{message.sender}

Related task:
{message.task_id or "None"}

Event:
{message.content}

Current task graph:

{json.dumps(current_tasks, indent=2)}

Available agents:

{agent_list}

Only change the graph when the PLAN itself needs changing.

The runtime already manages normal task status transitions.

A workspace-step limit is an execution-loop failure.
It does not prove the assigned agent lacks the required capability.
Do not reassign solely because a step limit was reached.

For ADD:
- include the complete task object
- use a unique task ID
- use an existing agent as owner

For REVISE:
- task_id must exist
- dependencies must reference existing tasks or tasks added
  in this same replan

For REASSIGN:
- task_id is required
- owner is required
- owner must exactly match an existing agent
- never use null or an empty owner
- do not reassign to the same owner

If the plan is still appropriate, return no actions.
""".strip()

        result, _ = await self._structured_chat(
            [
                {
                    "role": "system",
                    "content": (
                        self.agent.build_system_prompt(
                            self.project
                        )
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            schema=REPLAN_SCHEMA,
            max_tokens=4096,
        )

        elapsed = (
            time.perf_counter()
            - start_time
        )

        console.print(
            f"[bright_magenta]✓[/bright_magenta] "
            f"[bold]{self.agent.name}[/bold] "
            f"[dim]reviewed project state "
            f"in {elapsed:.1f}s[/dim]"
        )

        await self.hub.send(
            Message(
                sender=self.agent.name,
                recipient="__runtime__",
                type="REPLAN_RESULT",
                task_id=message.task_id,
                content=json.dumps(
                    result
                ),
                metadata={
                    "elapsed": elapsed,
                },
            )
        )

    #
    # Task execution
    #

    async def _handle_task(
        self,
        message,
    ):
        task = message.metadata[
            "task"
        ]

        start_time = time.perf_counter()

        console.print(
            f"[green]▶[/green] "
            f"[bold]{self.agent.name}[/bold] "
            f"[dim]{task.id} — "
            f"{task.title}[/dim]"
        )

        acceptance = "\n".join(
            f"- {item}"
            for item
            in task.acceptance
        )

        agent_list = "\n".join(
            f"- {agent.name}"
            for agent
            in self.project.agents
        )

        prompt = f"""
Execute the assigned task.

ID:
{task.id}

Title:
{task.title}

Description:
{task.description}

Acceptance criteria:
{acceptance}

Workspace:
{self.workspace.root}

WORKSPACE TOOLS

Use paths RELATIVE to the workspace.

Examples:

Correct:
  .
  README.md
  src/main.js

Incorrect:
  threejs-solar/src/main.js
  /home/user/project/src/main.js


LIST
List a directory.

Example:
{{"tool":"list","path":"."}}

Do NOT put directory contents into the request.
MAD will return the directory contents after the tool runs.


READ
Read a text file.

Example:
{{"tool":"read","path":"src/main.js"}}


WRITE
Create or replace a text file.

Example:
{{"tool":"write","path":"README.md","content":"..."}}


APPEND
Append to a text file.

Example:
{{"tool":"append","path":"notes.md","content":"..."}}


RUN
Run a command expected to terminate.

Example:
{{"tool":"run","command":"npm test"}}


SERVE
Start a long-running background process.

Example:
{{"tool":"serve","command":"npm start","name":"dev"}}


STOP_SERVER
Stop a background process.

Example:
{{"tool":"stop_server","name":"dev"}}


Only include fields relevant to the requested tool.

Perform actual workspace work when the task requires artifacts.

Follow your role instructions and assigned skills.

Available agents:

{agent_list}

Use exact agent names when communicating.
""".strip()

        conversation = [
            {
                "role": "system",
                "content": (
                    self.agent.build_system_prompt(
                        self.project
                    )
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ]

        max_steps = 20
        final_result = None

        for step in range(
            1,
            max_steps + 1,
        ):
            #
            # Decide whether another tool step is required.
            #

            decision, _ = (
                await self._structured_chat(
                    conversation
                    + [
                        {
                            "role": "user",
                            "content": (
                                "Choose the next action. "
                                "Return state='tool' if workspace "
                                "work remains. Return state='final' "
                                "only if the task is completed "
                                "or genuinely blocked."
                            ),
                        }
                    ],
                    schema=TASK_DECISION_SCHEMA,
                    max_tokens=256,
                )
            )

            state = decision[
                "state"
            ]

            #
            # Tool step
            #

            if state == "tool":
                tool_result, tool_response = (
                    await self._structured_chat(
                        conversation
                        + [
                            {
                                "role": "user",
                                "content": (
                                    "Request only the workspace "
                                    "operations needed next. "
                                    "Do not predict or invent tool "
                                    "results. MAD will execute the "
                                    "tools and return their results."
                                ),
                            }
                        ],
                        schema=TOOL_STEP_SCHEMA,
                        max_tokens=8192,
                    )
                )

                await self._route_messages(
                    tool_result.get(
                        "messages",
                        [],
                    ),
                    task_id=task.id,
                )

                tool_calls = tool_result.get(
                    "tool_calls",
                    [],
                )

                if not tool_calls:
                    conversation.append(
                        {
                            "role": "user",
                            "content": (
                                "You selected a tool step but "
                                "requested no workspace operations."
                            ),
                        }
                    )
                    continue

                console.print(
                    f"[dim]"
                    f"  {self.agent.name}: "
                    f"workspace step {step}"
                    f"[/dim]"
                )

                results = []

                for call in tool_calls:
                    tool = call[
                        "tool"
                    ]

                    path = call.get(
                        "path",
                        "",
                    )

                    content = call.get(
                        "content",
                        "",
                    )

                    command = call.get(
                        "command",
                        "",
                    )

                    name = call.get(
                        "name",
                        "",
                    )

                    #
                    # Reject absolute paths.
                    #

                    if (
                        path
                        and path.startswith("/")
                    ):
                        error = (
                            "Workspace paths must be relative."
                        )

                        console.print(
                            f"[yellow]"
                            f"    {tool} rejected: "
                            f"{path}"
                            f"[/yellow]"
                        )

                        results.append(
                            {
                                "tool": tool,
                                "success": False,
                                "result": error,
                            }
                        )

                        continue

                    #
                    # Reject paths that redundantly include
                    # the workspace directory name.
                    #

                    workspace_name = (
                        self.workspace.root.name
                    )

                    if (
                        path == workspace_name
                        or path.startswith(
                            workspace_name + "/"
                        )
                    ):
                        error = (
                            "Path is already relative to the "
                            "project workspace. Do not include "
                            f"{workspace_name!r} in the path."
                        )

                        console.print(
                            f"[yellow]"
                            f"    {tool} rejected: "
                            f"{path}"
                            f"[/yellow]"
                        )

                        results.append(
                            {
                                "tool": tool,
                                "success": False,
                                "result": error,
                            }
                        )

                        continue

                    try:
                        result_text = (
                            self.workspace.execute_tool(
                                tool=tool,
                                path=path,
                                content=content,
                                command=command,
                                name=name,
                            )
                        )

                        target = (
                            path
                            or command
                            or name
                            or ""
                        )

                        console.print(
                            f"[dim]"
                            f"    {tool} {target}"
                            f"[/dim]"
                        )

                        results.append(
                            {
                                "tool": tool,
                                "success": True,
                                "result": result_text,
                            }
                        )

                    except Exception as error:
                        console.print(
                            f"[yellow]"
                            f"    {tool} failed: "
                            f"{error}"
                            f"[/yellow]"
                        )

                        results.append(
                            {
                                "tool": tool,
                                "success": False,
                                "result": str(
                                    error
                                ),
                            }
                        )

                conversation.append(
                    {
                        "role": "assistant",
                        "content": tool_response,
                    }
                )

                conversation.append(
                    {
                        "role": "user",
                        "content": (
                            "WORKSPACE TOOL RESULTS:\n\n"
                            + json.dumps(
                                results,
                                indent=2,
                            )
                            + "\n\nContinue the task. "
                            "Use these actual results rather "
                            "than inventing workspace state."
                        ),
                    }
                )

                continue

            #
            # Final result
            #

            final_data, _ = (
                await self._structured_chat(
                    conversation
                    + [
                        {
                            "role": "user",
                            "content": (
                                "Return the final task result. "
                                "Do not request any tools."
                            ),
                        }
                    ],
                    schema=FINAL_STEP_SCHEMA,
                    max_tokens=4096,
                )
            )

            await self._route_messages(
                final_data.get(
                    "messages",
                    [],
                ),
                task_id=task.id,
            )

            final_result = {
                "status": final_data[
                    "status"
                ],
                "summary": final_data[
                    "summary"
                ],
                "output": final_data[
                    "output"
                ],
            }

            break

        if final_result is None:
            final_result = {
                "status": "blocked",
                "summary": (
                    f"Maximum workspace steps "
                    f"({max_steps}) reached."
                ),
                "output": (
                    "Execution stopped because the agent "
                    "did not converge within the workspace "
                    "tool-step limit."
                ),
            }

        elapsed = (
            time.perf_counter()
            - start_time
        )

        status = final_result[
            "status"
        ]

        if status == "completed":
            console.print(
                f"[bold green]✓[/bold green] "
                f"[bold]{self.agent.name}[/bold] "
                f"[dim]{task.id} completed "
                f"in {elapsed:.1f}s[/dim]"
            )

        else:
            console.print(
                f"[bold red]✗[/bold red] "
                f"[bold]{self.agent.name}[/bold] "
                f"[dim]{task.id} blocked "
                f"after {elapsed:.1f}s[/dim]"
            )

        await self.hub.send(
            Message(
                sender=self.agent.name,
                recipient="__runtime__",
                type="TASK_RESULT",
                task_id=task.id,
                content=final_result[
                    "summary"
                ],
                metadata={
                    "status": status,
                    "elapsed": elapsed,
                    "output": final_result[
                        "output"
                    ],
                },
            )
        )

    #
    # Peer communication
    #

    async def _handle_peer_message(
        self,
        message,
    ):
        start_time = time.perf_counter()

        message_type = message.metadata.get(
            "message_type",
            "message",
        )

        console.print(
            f"[cyan]◆[/cyan] "
            f"[bold]{self.agent.name}[/bold] "
            f"[dim]← {message.sender} "
            f"({message_type})[/dim]"
        )

        agent_list = "\n".join(
            f"- {agent.name}"
            for agent
            in self.project.agents
        )

        prompt = f"""
You received a message from another project agent.

From:
{message.sender}

Type:
{message_type}

Related task:
{message.task_id or "None"}

Message:
{message.content}

Available agents:

{agent_list}

Consider the message according to your role and skills.

Respond only when useful.
Use exact agent names.
""".strip()

        result, _ = await self._structured_chat(
            [
                {
                    "role": "system",
                    "content": (
                        self.agent.build_system_prompt(
                            self.project
                        )
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            schema=MESSAGE_SCHEMA,
            max_tokens=1024,
        )

        elapsed = (
            time.perf_counter()
            - start_time
        )

        console.print(
            f"[cyan]✓[/cyan] "
            f"[bold]{self.agent.name}[/bold] "
            f"[dim]processed message "
            f"in {elapsed:.1f}s[/dim]"
        )

        await self._route_messages(
            result.get(
                "messages",
                [],
            ),
            task_id=message.task_id,
        )

    #
    # Message routing
    #

    async def _route_messages(
        self,
        messages,
        task_id=None,
    ):
        valid_agents = {
            agent.name
            for agent
            in self.project.agents
        }

        for outgoing in messages:
            recipient = outgoing.get(
                "recipient"
            )

            if recipient == self.agent.name:
                continue

            if recipient not in valid_agents:
                console.print(
                    f"[yellow]"
                    f"⚠ {self.agent.name} tried "
                    f"to message unknown agent "
                    f"{recipient!r}"
                    f"[/yellow]"
                )

                continue

            await self.hub.send(
                Message(
                    sender=self.agent.name,
                    recipient=recipient,
                    type="PEER",
                    task_id=task_id,
                    content=outgoing.get(
                        "content",
                        "",
                    ),
                    metadata={
                        "message_type": (
                            outgoing.get(
                                "type",
                                "message",
                            )
                        ),
                    },
                )
            )
