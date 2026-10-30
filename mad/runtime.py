import asyncio
import copy
import json
import re

from mad.agent_runtime import AgentRuntime
from mad.llm import LLM
from mad.message import Message
from mad.message_hub import MessageHub
from mad.task import Task
from mad.workspace import Workspace
from mad.ui import (
    console,
    print_banner,
    print_project,
)


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


class Runtime:
    def __init__(
        self,
        project,
        server="http://localhost:8000/v1",
        show_thinking=False,
        verbose=False,
    ):
        self.project = project
        self.server = server
        self.show_thinking = show_thinking
        self.verbose = verbose

        self.workspace = Workspace(
            project.workspace
        )

        self.hub = MessageHub()

        self.agent_runtimes = {}
        self.runtime_tasks = []

        self.running_tasks = set()
        self.completed_tasks = set()

        self.pending_replans = 0

        self.director = None

    async def run(self):
        self._initialize_agents()

        print_banner()

        print_project(
            self.project,
            verbose=self.verbose,
        )

        console.print(
            f"[dim]"
            f"Workspace: {self.workspace.root}"
            f"[/dim]"
        )

        console.print()

        self._find_director()

        for runtime in self.agent_runtimes.values():
            self.runtime_tasks.append(
                asyncio.create_task(
                    runtime.run()
                )
            )

        await self.hub.send(
            Message(
                sender="__runtime__",
                recipient=self.director.name,
                type="PLAN_REQUEST",
                metadata={
                    "agents": [
                        agent.name
                        for agent
                        in self.project.agents
                    ],
                },
            )
        )

        try:
            await self._runtime_loop()

        finally:
            await self._shutdown()

    #
    # Initialization
    #

    def _initialize_agents(self):
        self.hub.register(
            "__runtime__"
        )

        for agent in self.project.agents:
            self.hub.register(
                agent.name
            )

            agent.server = LLM(
                base_url=self.server,
                show_thinking=self.show_thinking,
            )

            agent.message_hub = self.hub

            self.agent_runtimes[
                agent.name
            ] = AgentRuntime(
                agent=agent,
                project=self.project,
                hub=self.hub,
                workspace=self.workspace,
            )

    def _find_director(self):
        directors = [
            agent
            for agent in self.project.agents
            if agent.template == "Director.md"
        ]

        if len(directors) != 1:
            raise ValueError(
                "MAD projects require exactly one Director."
            )

        self.director = directors[
            0
        ]

    #
    # Runtime loop
    #

    async def _runtime_loop(self):
        while True:
            message = await self.hub.receive(
                "__runtime__"
            )

            if message.type == "PLAN_RESULT":
                await self._handle_plan(
                    message
                )

            elif message.type == "TASK_RESULT":
                await self._handle_task_result(
                    message
                )

            elif message.type == "REPLAN_RESULT":
                await self._handle_replan_result(
                    message
                )

            if self._project_finished():
                break

    #
    # Initial plan
    #

    async def _handle_plan(
        self,
        message,
    ):
        data = parse_json_response(
            message.content
        )

        self.project.tasks = [
            Task.from_dict(
                task_data
            )
            for task_data
            in data["tasks"]
        ]

        self._validate_tasks(
            self.project.tasks
        )

        console.print()
        console.print(
            "[bold cyan]"
            "Initial Task Graph"
            "[/bold cyan]"
        )

        for task in self.project.tasks:
            console.print(
                f"  [yellow]{task.id}[/yellow] "
                f"{task.title} "
                f"[dim]→ {task.owner}[/dim]"
            )

        console.print()

        await self._dispatch_ready_tasks()

    #
    # Worker result
    #

    async def _handle_task_result(
        self,
        message,
    ):
        task = self._get_task(
            message.task_id
        )

        self.running_tasks.discard(
            task.id
        )

        proposed_status = message.metadata.get(
            "status",
            "blocked",
        )

        elapsed = message.metadata.get(
            "elapsed"
        )

        output = message.metadata.get(
            "output",
            "",
        )

        changed_files = message.metadata.get(
            "changed_files",
            [],
        )

        browser_checks = message.metadata.get(
            "browser_checks",
            [],
        )

        command_results = message.metadata.get(
            "command_results",
            [],
        )

        summary = (
            message.content
            or ""
        )

        #
        # Worker success does NOT mean completed.
        #

        if proposed_status == "completed":
            task.status = "review"

            timing = (
                f" in {elapsed:.1f}s"
                if elapsed is not None
                else ""
            )

            console.print(
                f"[bold yellow]"
                f"◇ {task.id} ready for review"
                f"[/bold yellow] "
                f"{task.title} "
                f"[dim]"
                f"({message.sender}{timing})"
                f"[/dim]"
            )

            audit_status = "review"

        else:
            task.status = "blocked"

            timing = (
                f" after {elapsed:.1f}s"
                if elapsed is not None
                else ""
            )

            console.print(
                f"[bold red]"
                f"✗ {task.id} blocked"
                f"[/bold red] "
                f"[dim]"
                f"({message.sender}{timing})"
                f"[/dim]"
            )

            audit_status = "blocked"

        artifact_path = (
            self.workspace.write_task_result(
                task=task,
                agent_name=message.sender,
                status=audit_status,
                summary=summary,
                output=output,
            )
        )

        console.print(
            f"[dim]"
            f"↳ audit: {artifact_path}"
            f"[/dim]"
        )

        if summary:
            console.print()
            console.print(
                f"[bold cyan]"
                f"{message.sender}"
                f"[/bold cyan]"
            )
            console.print(
                summary
            )
            console.print()

        if output:
            console.print(
                f"[bold white]"
                f"{message.sender} result"
                f"[/bold white]"
            )
            console.print(
                output
            )
            console.print()

        #
        # Runtime supplies Director with independent
        # workspace evidence.
        #

        try:
            workspace_snapshot = await asyncio.to_thread(
                self.workspace.list_files,
                ".",
            )

        except Exception as error:
            workspace_snapshot = (
                f"[workspace listing failed: {error}]"
            )

        evidence = [
            f"TASK: {task.id} — {task.title}",
            f"WORKER: {message.sender}",
            f"WORKER CLAIMED STATUS: {proposed_status}",
            "",
            "ACCEPTANCE CRITERIA:",
        ]

        if task.acceptance:
            evidence.extend(
                f"- {item}"
                for item in task.acceptance
            )

        else:
            evidence.append(
                "(none)"
            )

        evidence.extend(
            [
                "",
                "WORKER SUMMARY:",
                summary or "(none)",
                "",
                "WORKER OUTPUT:",
                output or "(none)",
                "",
                "FILES CHANGED BY THIS TASK:",
            ]
        )

        if changed_files:
            evidence.extend(
                f"- {path}"
                for path in changed_files
            )

        else:
            evidence.append(
                "(none recorded)"
            )

        evidence.extend(
            [
                "",
                "CURRENT WORKSPACE SNAPSHOT:",
                workspace_snapshot,
            ]
        )

        if browser_checks:
            evidence.extend(
                [
                    "",
                    "BROWSER CHECKS:",
                ]
            )

            for check in browser_checks:
                evidence.extend(
                    [
                        "---",
                        check,
                    ]
                )

        if command_results:
            evidence.extend(
                [
                    "",
                    "COMMAND RESULTS:",
                ]
            )

            for result in command_results[-5:]:
                evidence.extend(
                    [
                        "---",
                        result,
                    ]
                )

        evidence.extend(
            [
                "",
                f"AUDIT RECORD: {artifact_path}",
            ]
        )

        self.pending_replans += 1

        await self.hub.send(
            Message(
                sender=message.sender,
                recipient=self.director.name,
                type="REPLAN_REQUEST",
                task_id=task.id,
                content="\n".join(
                    evidence
                ),
                metadata={
                    "event_type": (
                        "task_review"
                        if proposed_status == "completed"
                        else "blocker"
                    ),
                    "status": task.status,
                    "artifact": str(
                        artifact_path
                    ),
                },
            )
        )

    #
    # Director result
    #

    async def _handle_replan_result(
        self,
        message,
    ):
        if self.pending_replans > 0:
            self.pending_replans -= 1

        data = parse_json_response(
            message.content
        )

        reason = data.get(
            "reason",
            "",
        )

        actions = data.get(
            "actions",
            [],
        )

        console.print()
        console.print(
            "[bold magenta]"
            "Director Review"
            "[/bold magenta]"
        )

        if reason:
            console.print(
                f"[dim]{reason}[/dim]"
            )

        reviewed_task = self._find_task(
            message.task_id
        )

        #
        # A review task may NOT silently pass with no action.
        #

        if (
            reviewed_task
            and reviewed_task.status == "review"
            and not actions
        ):
            console.print(
                "[yellow]"
                "⚠ Director did not explicitly accept "
                "the reviewed task. Reopening it."
                "[/yellow]"
            )

            reviewed_task.status = "pending"

            await self._dispatch_ready_tasks()
            return

        if not actions:
            console.print(
                "[dim]"
                "No task graph changes."
                "[/dim]"
            )

            await self._dispatch_ready_tasks()
            return

        proposed_tasks = copy.deepcopy(
            self.project.tasks
        )

        valid = self._apply_replan_actions(
            actions,
            proposed_tasks,
        )

        if not valid:
            console.print(
                "[bold yellow]"
                "⚠ Replan rejected. "
                "Existing task graph unchanged."
                "[/bold yellow]"
            )

            #
            # Don't strand review tasks.
            #

            if (
                reviewed_task
                and reviewed_task.status == "review"
            ):
                reviewed_task.status = "pending"

            await self._dispatch_ready_tasks()
            return

        try:
            self._validate_tasks(
                proposed_tasks
            )

        except ValueError as error:
            console.print(
                "[bold yellow]"
                f"⚠ Replan rejected: {error}"
                "[/bold yellow]"
            )

            if (
                reviewed_task
                and reviewed_task.status == "review"
            ):
                reviewed_task.status = "pending"

            await self._dispatch_ready_tasks()
            return

        self.project.tasks = (
            proposed_tasks
        )

        self._synchronize_completed_tasks()

        console.print(
            "[green]"
            "✓ Director decision accepted"
            "[/green]"
        )

        await self._dispatch_ready_tasks()

    #
    # Replan actions
    #

    def _apply_replan_actions(
        self,
        actions,
        tasks,
    ):
        planned_add_ids = set()

        for action in actions:
            if action.get(
                "type"
            ) != "add":
                continue

            task_data = action.get(
                "task"
            )

            if not task_data:
                console.print(
                    "[yellow]"
                    "⚠ Director ADD missing complete task."
                    "[/yellow]"
                )
                return False

            task_id = task_data.get(
                "id"
            )

            if not task_id:
                console.print(
                    "[yellow]"
                    "⚠ Director ADD task missing ID."
                    "[/yellow]"
                )
                return False

            planned_add_ids.add(
                task_id
            )

        for action in actions:
            if not self._apply_replan_action(
                action,
                tasks,
                planned_add_ids,
            ):
                return False

        return True

    def _apply_replan_action(
        self,
        action,
        tasks,
        planned_add_ids,
    ):
        action_type = action.get(
            "type"
        )

        if action_type == "accept":
            return self._action_accept(
                action,
                tasks,
            )

        if action_type == "add":
            return self._action_add(
                action,
                tasks,
            )

        if action_type == "revise":
            return self._action_revise(
                action,
                tasks,
                planned_add_ids,
            )

        if action_type == "reassign":
            return self._action_reassign(
                action,
                tasks,
            )

        if action_type == "cancel":
            return self._action_cancel(
                action,
                tasks,
            )

        if action_type == "reopen":
            return self._action_reopen(
                action,
                tasks,
            )

        console.print(
            f"[yellow]"
            f"⚠ Unknown replan action "
            f"{action_type!r}"
            f"[/yellow]"
        )

        return False

    #
    # ACCEPT
    #

    def _action_accept(
        self,
        action,
        tasks,
    ):
        task_id = action.get(
            "task_id"
        )

        task = self._find_task_in(
            tasks,
            task_id,
        )

        if not task:
            console.print(
                f"[yellow]"
                f"⚠ Cannot accept unknown task "
                f"{task_id!r}"
                f"[/yellow]"
            )
            return False

        if task.status != "review":
            console.print(
                f"[yellow]"
                f"⚠ Cannot accept {task.id}: "
                f"status is {task.status!r}, "
                f"expected 'review'"
                f"[/yellow]"
            )
            return False

        task.status = "completed"

        console.print(
            f"[bold green]"
            f"✓ {task.id} accepted"
            f"[/bold green]"
        )

        return True

    #
    # ADD
    #

    def _action_add(
        self,
        action,
        tasks,
    ):
        task_data = action.get(
            "task"
        )

        if not task_data:
            return False

        try:
            task = Task.from_dict(
                task_data
            )

        except Exception as error:
            console.print(
                f"[yellow]"
                f"⚠ Invalid ADD task: {error}"
                f"[/yellow]"
            )
            return False

        if self._find_task_in(
            tasks,
            task.id,
        ):
            console.print(
                f"[yellow]"
                f"⚠ Task already exists: "
                f"{task.id}"
                f"[/yellow]"
            )
            return False

        tasks.append(
            task
        )

        console.print(
            f"[green]+[/green] "
            f"{task.id} "
            f"{task.title} "
            f"[dim]→ {task.owner}[/dim]"
        )

        return True

    #
    # REVISE
    #

    def _action_revise(
        self,
        action,
        tasks,
        planned_add_ids,
    ):
        task_id = action.get(
            "task_id"
        )

        task = self._find_task_in(
            tasks,
            task_id,
        )

        if not task:
            console.print(
                f"[yellow]"
                f"⚠ Cannot revise unknown task "
                f"{task_id!r}"
                f"[/yellow]"
            )
            return False

        if task.status == "running":
            console.print(
                f"[yellow]"
                f"⚠ Cannot revise running task "
                f"{task_id}"
                f"[/yellow]"
            )
            return False

        updates = action.get(
            "updates",
            {},
        )

        if "dependencies" in updates:
            existing = {
                item.id
                for item in tasks
            }

            allowed = (
                existing
                | planned_add_ids
            )

            for dependency in updates[
                "dependencies"
            ]:
                if dependency not in allowed:
                    console.print(
                        f"[yellow]"
                        f"⚠ {task_id} references unknown "
                        f"dependency {dependency!r}"
                        f"[/yellow]"
                    )
                    return False

        for field in [
            "title",
            "description",
            "owner",
            "dependencies",
            "acceptance",
        ]:
            if field in updates:
                setattr(
                    task,
                    field,
                    updates[field],
                )

        task.status = "pending"

        console.print(
            f"[yellow]~[/yellow] "
            f"{task.id} revised"
        )

        return True

    #
    # REASSIGN
    #

    def _action_reassign(
        self,
        action,
        tasks,
    ):
        task_id = action.get(
            "task_id"
        )

        owner = action.get(
            "owner"
        )

        task = self._find_task_in(
            tasks,
            task_id,
        )

        if not task:
            return False

        if not owner:
            console.print(
                f"[yellow]"
                f"⚠ Invalid reassignment of "
                f"{task_id}: missing owner"
                f"[/yellow]"
            )
            return False

        valid_agents = {
            agent.name
            for agent in self.project.agents
        }

        if owner not in valid_agents:
            console.print(
                f"[yellow]"
                f"⚠ Unknown agent {owner!r}"
                f"[/yellow]"
            )
            return False

        if task.status == "running":
            return False

        if task.owner == owner:
            console.print(
                f"[yellow]"
                f"⚠ {task.id} already belongs "
                f"to {owner}"
                f"[/yellow]"
            )
            return False

        task.owner = owner
        task.status = "pending"

        console.print(
            f"[yellow]→[/yellow] "
            f"{task.id} reassigned "
            f"to {owner}"
        )

        return True

    #
    # CANCEL
    #

    def _action_cancel(
        self,
        action,
        tasks,
    ):
        task_id = action.get(
            "task_id"
        )

        task = self._find_task_in(
            tasks,
            task_id,
        )

        if not task:
            return False

        if task.status == "running":
            return False

        task.status = "cancelled"

        console.print(
            f"[red]×[/red] "
            f"{task.id} cancelled"
        )

        return True

    #
    # REOPEN
    #

    def _action_reopen(
        self,
        action,
        tasks,
    ):
        task_id = action.get(
            "task_id"
        )

        task = self._find_task_in(
            tasks,
            task_id,
        )

        if not task:
            return False

        if task.status == "running":
            return False

        task.status = "pending"

        console.print(
            f"[yellow]↻[/yellow] "
            f"{task.id} reopened"
        )

        return True

    #
    # Scheduler
    #

    async def _dispatch_ready_tasks(
        self,
    ):
        for task in self.project.tasks:
            if task.status != "pending":
                continue

            dependencies_complete = all(
                dependency
                in self.completed_tasks
                for dependency
                in task.dependencies
            )

            if not dependencies_complete:
                continue

            task.status = "running"

            self.running_tasks.add(
                task.id
            )

            await self.hub.send(
                Message(
                    sender=self.director.name,
                    recipient=task.owner,
                    type="TASK",
                    task_id=task.id,
                    metadata={
                        "task": task,
                    },
                )
            )

    #
    # Validation
    #

    def _validate_tasks(
        self,
        tasks,
    ):
        agent_names = {
            agent.name
            for agent in self.project.agents
        }

        task_ids = [
            task.id
            for task in tasks
        ]

        if len(task_ids) != len(
            set(task_ids)
        ):
            raise ValueError(
                "Duplicate task IDs."
            )

        task_id_set = set(
            task_ids
        )

        for task in tasks:
            if task.owner not in agent_names:
                raise ValueError(
                    f"{task.id} has invalid owner "
                    f"{task.owner!r}"
                )

            for dependency in task.dependencies:
                if dependency not in task_id_set:
                    raise ValueError(
                        f"{task.id} has unknown dependency "
                        f"{dependency!r}"
                    )

                if dependency == task.id:
                    raise ValueError(
                        f"{task.id} depends on itself."
                    )

    def _synchronize_completed_tasks(
        self,
    ):
        self.completed_tasks = {
            task.id
            for task in self.project.tasks
            if task.status == "completed"
        }

    #
    # Helpers
    #

    def _find_task_in(
        self,
        tasks,
        task_id,
    ):
        for task in tasks:
            if task.id == task_id:
                return task

        return None

    def _find_task(
        self,
        task_id,
    ):
        return self._find_task_in(
            self.project.tasks,
            task_id,
        )

    def _get_task(
        self,
        task_id,
    ):
        task = self._find_task(
            task_id
        )

        if task is None:
            raise ValueError(
                f"Unknown task: {task_id}"
            )

        return task

    #
    # Completion
    #

    def _project_finished(
        self,
    ):
        if not hasattr(
            self.project,
            "tasks",
        ):
            return False

        if self.running_tasks:
            return False

        if self.pending_replans:
            return False

        #
        # Anything under Director review means
        # the project is definitely not finished.
        #

        if any(
            task.status == "review"
            for task in self.project.tasks
        ):
            return False

        pending = [
            task
            for task in self.project.tasks
            if task.status == "pending"
        ]

        if pending:
            ready = [
                task
                for task in pending
                if all(
                    dependency
                    in self.completed_tasks
                    for dependency
                    in task.dependencies
                )
            ]

            if ready:
                return False

            console.print()
            console.print(
                "[yellow]"
                "No runnable tasks remain. "
                "The project may be blocked."
                "[/yellow]"
            )

            return True

        blocked = [
            task
            for task in self.project.tasks
            if task.status == "blocked"
        ]

        if blocked:
            console.print()
            console.print(
                "[yellow]"
                f"Project ended with "
                f"{len(blocked)} blocked task(s)."
                "[/yellow]"
            )

        return True

    #
    # Shutdown
    #

    async def _shutdown(
        self,
    ):
        for result in self.workspace.stop_servers():
            console.print(
                f"[dim]{result}[/dim]"
            )

        for agent in self.project.agents:
            await self.hub.send(
                Message(
                    sender="__runtime__",
                    recipient=agent.name,
                    type="STOP",
                )
            )

        if self.runtime_tasks:
            await asyncio.gather(
                *self.runtime_tasks,
                return_exceptions=True,
            )
