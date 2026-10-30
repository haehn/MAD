"""Compose MAD services and orchestrate project messages and task scheduling."""
import asyncio
from mad.agent_runtime import AgentRuntime
from mad.llm import LLM
from mad.message import Message
from mad.message_hub import MessageHub
from mad.protocol import parse_json_response
from mad.review_service import ReviewService
from mad.project_state import ProjectState
from mad.reporter import Reporter
from mad.task import Task
from mad.workspace import Workspace


class Runtime:
    def __init__(self, project, server='http://localhost:8000/v1',
                 show_thinking=False, verbose=False, model_factory=None,
                 reporter=None, workspace=None):
        self.project = project
        self.server = server
        self.show_thinking = show_thinking
        self.verbose = verbose
        self.workspace = workspace if workspace is not None else Workspace(project.workspace)
        self.reporter = reporter if reporter is not None else Reporter()
        self.model_factory = model_factory or (lambda agent: LLM(
            base_url=server, show_thinking=show_thinking))
        self.state = ProjectState(project)
        self.hub = MessageHub()
        self.agent_runtimes = {}
        self.runtime_tasks = []
        self.pending_reviews = set()
        self.supervisor = None
        self.project_accepted = False
        self.project_review_pending = False
        self.project_review_attempted = False
        self.project_evidence = {}
        self.task_feedback = {}
        self.review_attempts = {}

    @property
    def running_tasks(self):
        return self.state.running

    @property
    def completed_tasks(self):
        return self.state.completed

    @property
    def pending_replans(self):
        return len(self.pending_reviews)

    async def run(self):
        self._find_supervisor()
        self._initialize_agents()
        self.reporter.start(self.project, self.workspace.root, self.verbose)
        try:
            self.runtime_tasks = [asyncio.create_task(runtime.run())
                                  for runtime in self.agent_runtimes.values()]
            await self.hub.send(Message(
                sender='__runtime__', recipient=self.supervisor.name, type='PLAN_REQUEST',
                metadata={'agents': [agent.name for agent in self.project.agents]},
            ))
            await self._runtime_loop()
        finally:
            await self._shutdown()

    def _initialize_agents(self):
        names = [agent.name for agent in self.project.agents]
        if len(names) != len(set(names)) or '__runtime__' in names:
            raise ValueError('Agent names must be unique and cannot be __runtime__.')
        self.hub.register('__runtime__')
        for agent in self.project.agents:
            self.hub.register(agent.name)
            agent.server = self.model_factory(agent)
            agent.message_hub = self.hub
            self.agent_runtimes[agent.name] = AgentRuntime(
                agent, self.project, self.hub, self.workspace, reporter=self.reporter)

    def _find_supervisor(self):
        supervisors = [a for a in self.project.agents if a.template == 'Supervisor.md']
        if len(supervisors) != 1:
            raise ValueError('MAD projects require exactly one Supervisor.')
        self.supervisor = supervisors[0]

    def _require_supervisor(self, message):
        if self.supervisor is None or message.sender != self.supervisor.name:
            raise ValueError('Only the Supervisor may mutate the task plan or accept work.')

    async def _runtime_loop(self):
        handlers = {'PLAN_RESULT': self._handle_plan,
                    'TASK_RESULT': self._handle_task_result,
                    'REPLAN_RESULT': self._handle_replan_result,
                    'AGENT_ERROR': self._handle_agent_error,
                    'PROJECT_REVIEW_RESULT': self._handle_project_review_result}
        while True:
            message = await self.hub.receive('__runtime__')
            handler = handlers.get(message.type)
            if handler:
                await handler(message)
            if (self.state.finished(self.pending_reviews)
                    and all(task.status in {'completed', 'cancelled'} for task in self.state.tasks)
                    and not self.project_review_attempted):
                await self._request_project_review()
                continue
            if self._project_finished():
                self.reporter.finish(self.project.tasks)
                break

    async def _handle_plan(self, message):
        self._require_supervisor(message)
        if self.state.planned:
            raise ValueError('Initial task plan already installed.')
        self.state.install_plan(parse_json_response(message.content))
        self.reporter.plan(self.project.tasks)
        await self._dispatch_ready_tasks()

    async def _handle_task_result(self, message):
        self.review_attempts[message.task_id] = 0
        service = ReviewService(self.state, self.workspace, self.hub, self.supervisor,
                                self.reporter, self.pending_reviews)
        await service.submit(message)

    async def _handle_replan_result(self, message):
        self._require_supervisor(message)
        if message.task_id not in self.pending_reviews:
            raise ValueError(f'Unexpected Supervisor decision for {message.task_id}')
        self.pending_reviews.remove(message.task_id)
        evidence = self.state.evidence.setdefault(message.task_id, {})
        for record in message.metadata.get('review_evidence', []):
            evidence[record['id']] = record
        try:
            data = parse_json_response(message.content)
            self.state.decide(data.get('actions', []), message.task_id)
        except (ValueError, KeyError, TypeError) as error:
            self.workspace.audit_store.write_review(message.task_id, message.content, evidence, False, error)
            self.reporter.review_rejected(error, response=message.content)
            attempt = self.review_attempts.get(message.task_id, 0)
            if attempt >= 1:
                raise RuntimeError('Supervisor review remained invalid after correction; work was not accepted.') from error
            self.review_attempts[message.task_id] = attempt + 1
            self.pending_reviews.add(message.task_id)
            service = ReviewService(self.state, self.workspace, self.hub, self.supervisor,
                                    self.reporter, self.pending_reviews)
            context = await service.context(evidence)
            await self.hub.send(Message(
                sender='__runtime__', recipient=self.supervisor.name, type='REPLAN_REQUEST',
                task_id=message.task_id,
                content=f'Your previous decision was rejected: {error}\n'
                        f'Rejected response: {message.content}\n'
                        f'Correct your decision for task {message.task_id}; do not claim other tasks are complete.\n'
                        f'The task graph above is the current source of truth.\nRecorded evidence:\n{context}',
                metadata={'event_type': 'review_correction', 'review_attempt': attempt + 1,
                          'evidence_ids': list(evidence)},
            ))
            return
        else:
            self.workspace.audit_store.write_review(
                message.task_id, data, evidence,
                any(task.id == message.task_id and task.status == 'completed' for task in self.state.tasks),
            )
            self.task_feedback[message.task_id] = message.content
            self.reporter.review_accepted(data.get('reason', ''))
        await self._dispatch_ready_tasks()

    async def _handle_agent_error(self, message):
        request = message.metadata.get('request_type')
        if request == 'TASK':
            message.metadata['status'] = 'blocked'
            await self._handle_task_result(message)
        elif request in {'PLAN_REQUEST', 'REPLAN_REQUEST', 'PROJECT_REVIEW_REQUEST'}:
            self._require_supervisor(message)
            raise RuntimeError(f'Supervisor {request} failed: {message.content}')

    async def _dispatch_ready_tasks(self):
        for task in self.state.ready():
            self.state.start(task)
            await self.hub.send(Message(
                sender=self.supervisor.name, recipient=task.owner, type='TASK', task_id=task.id,
                metadata={'task': Task.from_dict(task.to_dict()),
                          'review_feedback': self.task_feedback.get(task.id, '')},
            ))

    async def _request_project_review(self):
        service = ReviewService(self.state, self.workspace, self.hub, self.supervisor,
                                self.reporter, self.pending_reviews)
        self.project_evidence, content = await service.project_context()
        self.project_review_pending = True
        self.project_review_attempted = True
        await self.hub.send(Message(
            sender='__runtime__', recipient=self.supervisor.name,
            type='PROJECT_REVIEW_REQUEST', content=content,
            metadata={'event_type': 'project_review', 'evidence_ids': list(self.project_evidence)},
        ))

    async def _handle_project_review_result(self, message):
        self._require_supervisor(message)
        if not self.project_review_pending:
            raise ValueError('Unexpected final project review.')
        self.project_review_pending = False
        for record in message.metadata.get('review_evidence', []):
            self.project_evidence[record['id']] = record
        try:
            data = parse_json_response(message.content)
            if not isinstance(data.get('accepted'), bool):
                raise ValueError('Final review must explicitly accept or reject the project.')
            if data['accepted']:
                if data.get('actions'):
                    raise ValueError('Task changes require a new final review.')
                self.state.validate_assessments(
                    self.project.success or [self.project.objective],
                    data.get('assessments'), self.project_evidence,
                )
                # Artifact projects require fresh inspection, not only earlier claims/listings.
                listing = self.project_evidence.get('workspace', {})
                if not listing.get('success'):
                    raise ValueError('Final workspace evidence is unavailable.')
                if listing.get('result') != '(directory is empty)':
                    fresh = {record['id'] for record in message.metadata.get('review_evidence', [])
                             if record.get('success') and record.get('request', {}).get('tool')
                             in {'read', 'run', 'browser_check'}}
                    if not fresh or not all(
                        fresh.intersection(item['evidence']) for item in data['assessments']
                    ):
                        raise ValueError('Final artifact review must cite fresh inspection evidence.')
                self.project_accepted = True
            else:
                before = [task.to_dict() for task in self.state.tasks]
                self.state.decide(data.get('actions', []), None)
                for action in data.get('actions', []):
                    if action.get('task_id'):
                        self.task_feedback[action['task_id']] = message.content
                # Another final review is required after corrective work.
                if [task.to_dict() for task in self.state.tasks] != before:
                    self.project_review_attempted = False
        except (ValueError, KeyError, TypeError) as error:
            self.workspace.audit_store.write_review('project', message.content,
                                                     self.project_evidence, False, error)
            self.reporter.review_rejected(error, response=message.content)
            self.reporter.project_review(False, str(error))
        else:
            self.workspace.audit_store.write_review('project', data,
                                                     self.project_evidence, self.project_accepted)
            self.reporter.project_review(self.project_accepted, data.get('reason', ''))
        await self._dispatch_ready_tasks()

    def _project_finished(self):
        return self.state.finished(self.pending_reviews) and not self.project_review_pending

    async def _shutdown(self):
        for result in await asyncio.to_thread(self.workspace.stop_servers):
            self.reporter.print(f'[dim]{result}[/dim]')
        for task in self.runtime_tasks:
            task.cancel()
        if self.runtime_tasks:
            await asyncio.gather(*self.runtime_tasks, return_exceptions=True)
