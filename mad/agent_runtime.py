import asyncio
import json
import re
import time

from mad.message import Message
from mad.ui import console


#
# Planning
#

TASK_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {
            "type": "string",
        },
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
    "required": [
        "tasks",
    ],
    "additionalProperties": False,
}


#
# Agent communication
#

MESSAGE_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "recipient": {
            "type": "string",
        },
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
        "content": {
            "type": "string",
        },
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
    "required": [
        "messages",
    ],
    "additionalProperties": False,
}


#
# Workspace tools
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
                "browser_check",
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
        "wait_ms": {
            "type": "integer",
            "minimum": 0,
            "maximum": 30000,
        },
    },
    "required": [
        "tool",
    ],
    "additionalProperties": False,
}


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
# Director replanning
#

REPLAN_ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "enum": [
                "accept",
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

    #
    # Model calls
    #

    async def _chat(
        self,
        messages,
        max_tokens=4096,
        json_schema=None,
        retries=2,
    ):
        last_error = None

        for attempt in range(
            retries + 1
        ):
            try:
                return await asyncio.to_thread(
                    self.agent.server.chat,
                    model=self.agent.model,
                    messages=messages,
                    max_tokens=max_tokens,
                    json_schema=json_schema,
                )

            except Exception as error:
                last_error = error

                if attempt >= retries:
                    raise

                console.print(
                    f"[yellow]"
                    f"⚠ {self.agent.name} model request failed "
                    f"(attempt {attempt + 1}/{retries + 1}): "
                    f"{error}"
                    f"[/yellow]"
                )

                await asyncio.sleep(
                    1.0
                )

        raise last_error

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
    # Initial planning
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

Use agents according to their roles and skills.

Keep the plan proportional to the project.

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
    # Director review / replanning
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
You are the Director and own the project's task graph.

Event type:
{event_type}

From:
{message.sender}

Related task:
{message.task_id or "None"}

Evidence and event:

{message.content}

Current task graph:

{json.dumps(current_tasks, indent=2)}

Available agents:

{agent_list}


DIRECTOR RESPONSIBILITY

Workers do NOT decide that their own work is accepted.

When a worker reports successful completion, the runtime places that
task into REVIEW.

You must decide whether the evidence is sufficient.

Use ACCEPT only when the task's acceptance criteria are credibly satisfied.

If evidence contradicts the worker's claim, do NOT accept it.

Examples of insufficient evidence:

- claimed files are absent from the workspace snapshot
- browser validation reports errors
- tests fail
- claimed output contradicts actual tool evidence
- required work was never created
- quality-control findings identify an unresolved defect


ACTIONS

ACCEPT
Approve a task currently in review.

Required:
- task_id

REOPEN
Return a task to pending so its owner performs additional work.

REVISE
Change the task itself and return it to pending.

REASSIGN
Move the task to a different appropriate agent.

ADD
Create a new task.

CANCEL
Cancel unnecessary work.


IMPORTANT

For a task in REVIEW you must either:

- ACCEPT it, or
- take corrective action on it.

Do not return no actions for a review task.

A workspace-step limit is an execution-loop failure.
It does not prove that the agent lacks the capability.
Do not reassign solely because of a step limit.

For ADD:
- include the complete task object
- use a unique ID
- owner must be an existing agent

For REASSIGN:
- task_id is mandatory
- owner is mandatory
- owner must exactly match an existing agent
- never use null or empty owner
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
                    "event_type": event_type,
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

Use paths relative to the workspace.

LIST
List a directory.

READ
Read a text file.

WRITE
Create or replace a text file.

APPEND
Append to a text file.

RUN
Run a command expected to terminate.

SERVE
Start a long-running background process.

STOP_SERVER
Stop a background process.

BROWSER_CHECK
Open a workspace HTML file in a real headless browser.

Example:

{{"tool":"browser_check","path":"index.html","wait_ms":1500}}

Tool results are ground truth.
Do not contradict or invent tool results.

READ an existing file before overwriting it.

A failed tool operation must be resolved or investigated before
you claim the task is completed.

Your completion claim will be reviewed independently by the Director.

Follow your role instructions and assigned skills.

Available agents:

{agent_list}

Use exact agent names.
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

        read_paths = set()
        changed_files = set()
        browser_checks = []
        command_results = []

        unresolved_tool_failure = False

        max_steps = 100
        final_result = None

        for step in range(
            1,
            max_steps + 1,
        ):
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

            if (
                state == "final"
                and unresolved_tool_failure
            ):
                conversation.append(
                    {
                        "role": "user",
                        "content": (
                            "The latest workspace step contained "
                            "a failed operation. Resolve or investigate "
                            "that failure before claiming completion. "
                            "If it cannot be resolved, report blocked."
                        ),
                    }
                )

                continue

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
                                    "Do not invent results."
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
                    unresolved_tool_failure = True

                    conversation.append(
                        {
                            "role": "user",
                            "content": (
                                "You selected a tool step but "
                                "requested no operations."
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
                step_failed = False

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

                    wait_ms = call.get(
                        "wait_ms",
                        1500,
                    )

                    #
                    # Path validation
                    #

                    if (
                        path
                        and path.startswith("/")
                    ):
                        error = (
                            "Workspace paths must be relative."
                        )

                        step_failed = True

                        results.append(
                            {
                                "tool": tool,
                                "success": False,
                                "result": error,
                            }
                        )

                        console.print(
                            f"[yellow]"
                            f"    {tool} rejected: {path}"
                            f"[/yellow]"
                        )

                        continue

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
                            "Do not include the workspace "
                            "directory name in tool paths."
                        )

                        step_failed = True

                        results.append(
                            {
                                "tool": tool,
                                "success": False,
                                "result": error,
                            }
                        )

                        console.print(
                            f"[yellow]"
                            f"    {tool} rejected: {path}"
                            f"[/yellow]"
                        )

                        continue

                    #
                    # Read before overwrite
                    #

                    if (
                        tool == "write"
                        and path
                        and self.workspace.exists(
                            path
                        )
                        and path not in read_paths
                    ):
                        error = (
                            f"{path} exists. "
                            "READ it before overwriting it."
                        )

                        step_failed = True

                        results.append(
                            {
                                "tool": tool,
                                "success": False,
                                "result": error,
                            }
                        )

                        console.print(
                            f"[yellow]"
                            f"    write rejected: "
                            f"{path} (read first)"
                            f"[/yellow]"
                        )

                        continue

                    try:
                        result_text = await asyncio.to_thread(
                            self.workspace.execute_tool,
                            tool=tool,
                            path=path,
                            content=content,
                            command=command,
                            name=name,
                            wait_ms=wait_ms,
                        )

                        #
                        # Evidence tracking
                        #

                        if (
                            tool == "read"
                            and path
                        ):
                            read_paths.add(
                                path
                            )

                        if (
                            tool in {
                                "write",
                                "append",
                            }
                            and path
                        ):
                            changed_files.add(
                                path
                            )

                            read_paths.add(
                                path
                            )

                        if tool == "browser_check":
                            browser_checks.append(
                                result_text
                            )

                            if "RESULT: FAIL" in result_text:
                                step_failed = True

                        if tool == "run":
                            command_results.append(
                                result_text
                            )

                            if "Return code: 0" not in result_text:
                                step_failed = True

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
                                "success": not (
                                    tool == "browser_check"
                                    and "RESULT: FAIL"
                                    in result_text
                                ),
                                "result": result_text,
                            }
                        )

                    except Exception as error:
                        step_failed = True

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

                unresolved_tool_failure = (
                    step_failed
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
                            + "\n\nTreat these results as ground truth "
                            "and continue the task."
                        ),
                    }
                )

                continue

            #
            # Final
            #

            final_data, _ = (
                await self._structured_chat(
                    conversation
                    + [
                        {
                            "role": "user",
                            "content": (
                                "Return your final task result. "
                                "Do not request tools. "
                                "The Director will independently "
                                "review your completion claim."
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
                    "Execution did not converge within "
                    "the workspace tool-step limit."
                ),
            }

        elapsed = (
            time.perf_counter()
            - start_time
        )

        proposed_status = final_result[
            "status"
        ]

        if proposed_status == "completed":
            console.print(
                f"[bold yellow]◇[/bold yellow] "
                f"[bold]{self.agent.name}[/bold] "
                f"[dim]{task.id} submitted for review "
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
                    "status": proposed_status,
                    "elapsed": elapsed,
                    "output": final_result[
                        "output"
                    ],
                    "changed_files": sorted(
                        changed_files
                    ),
                    "browser_checks": browser_checks,
                    "command_results": command_results,
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

Consider it according to your role and skills.

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
                        "message_type": outgoing.get(
                            "type",
                            "message",
                        ),
                    },
                )
            )
