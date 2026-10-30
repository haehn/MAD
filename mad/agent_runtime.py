"""Agent inbox lifecycle and dispatch to focused execution protocols."""
import time

from mad.supervisor_protocol import SupervisorProtocol
from mad.task_executor import TaskExecutor
from mad.message import Message
from mad.model_session import ModelSession
from mad.protocol import (
    TASK_SCHEMA, TASK_PLAN_SCHEMA, MESSAGE_ITEM_SCHEMA, MESSAGE_SCHEMA,
    TOOL_CALL_SCHEMA, TASK_DECISION_SCHEMA, TOOL_STEP_SCHEMA, FINAL_STEP_SCHEMA,
    REPLAN_ACTION_SCHEMA, REPLAN_SCHEMA, parse_json_response,
)
from mad.message_router import MessageRouter
from mad.reporter import Reporter


class AgentRuntime:
    def __init__(self, agent, project, hub, workspace, reporter=None):
        self.agent = agent
        self.project = project
        self.hub = hub
        self.workspace = workspace
        self.reporter = reporter if reporter is not None else Reporter()
        self.running = True
        self.model = ModelSession(agent, self.reporter)
        self.router = MessageRouter(agent, project, hub, self.reporter)
        self.supervisor = SupervisorProtocol(agent, project, hub, self.model, self.reporter, workspace)
        self.executor = TaskExecutor(agent, project, hub, workspace, self.model, self.router, self.reporter)

    async def run(self):
        handlers = {
            'PLAN_REQUEST': self.supervisor.plan,
            'REPLAN_REQUEST': self.supervisor.review,
            'PROJECT_REVIEW_REQUEST': self.supervisor.review,
            'TASK': self.executor.execute,
            'PEER': self._handle_peer_message,
        }
        while self.running:
            message = await self.hub.receive(self.agent.name)
            if message.type == 'STOP':
                self.running = False
                break
            try:
                if message.type in {'PLAN_REQUEST', 'REPLAN_REQUEST', 'PROJECT_REVIEW_REQUEST'} and self.agent.template != 'Supervisor.md':
                    raise ValueError('Only the Supervisor may plan or review tasks.')
                handler = handlers.get(message.type)
                if handler:
                    await handler(message)
            except Exception as error:
                self.reporter.print(f'[bold red]Agent {self.agent.name} failed:[/bold red] {error}')
                await self.hub.send(Message(
                    sender=self.agent.name, recipient='__runtime__', type='AGENT_ERROR',
                    task_id=message.task_id, content=str(error),
                    metadata={'request_type': message.type},
                ))

    async def _handle_peer_message(self, message):
        start = time.perf_counter()
        message_type = message.metadata.get('message_type', 'message')
        self.reporter.print(f'[cyan]◆[/cyan] [bold]{self.agent.name}[/bold] [dim]← {message.sender} ({message_type})[/dim]')
        agents = '\n'.join(f'- {agent.name}' for agent in self.project.agents)
        prompt = f"""You received a message from another project agent.

From:
{message.sender}

Type:
{message_type}

Related task:
{message.task_id or 'None'}

Message:
{message.content}

Available agents:

{agents}

Consider it according to your role and skills.

Respond only when useful.
Use exact agent names."""
        result, _ = await self.model.structured_chat([
            {'role': 'system', 'content': self.agent.build_system_prompt(self.project)},
            {'role': 'user', 'content': prompt},
        ], schema=MESSAGE_SCHEMA, max_tokens=1024)
        self.reporter.print(f'[cyan]✓[/cyan] [bold]{self.agent.name}[/bold] [dim]processed message in {time.perf_counter() - start:.1f}s[/dim]')
        await self.router.route(result.get('messages', []), task_id=message.task_id)
