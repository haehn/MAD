"""Connect agents, tasks, tools, and inboxes using one action handler."""
import asyncio
import json
from mad import ui
from mad.messaging import Message, MessageHub
from mad.project import ProjectState
from mad.workspace import Workspace


class Runtime:
    def __init__(self, project, server='http://localhost:8000/v1', show_thinking=False,
                 verbose=False):
        self.project, self.verbose = project, verbose
        self.state = ProjectState(project)
        self.workspace = Workspace(project.workspace)
        self.hub = MessageHub()
        self.server = server.rstrip('/')
        self.show_thinking = show_thinking
        self.pending_reviews, self.workers = set(), []
        self.finished = asyncio.Event()
        self.project_accepted, self.final_queued, self.error = False, False, None

    def _evidence(self, ids):
        return {ref: self.workspace.evidence[ref] for ref in ids if ref in self.workspace.evidence}

    def _context(self, event, task=None, records=None):
        data = {'event': event, 'workspace': str(self.workspace.root),
                'files': self.workspace.list_files(), 'task': task.to_dict() if task else None,
                'tasks': [item.to_dict() for item in self.state.tasks],
                'success': self.project.success or [self.project.objective],
                'evidence': records or {}}
        text = json.dumps(data, ensure_ascii=False)
        if len(text) > 50000:
            text = text[:50000] + '\n[Evidence excerpt truncated; inspect artifacts or request more material.]'
        if event == 'review':
            supervisor = next(agent for agent in self.project.agents if agent.name == self.state.supervisor)
            text = supervisor.review_instruction + '\n\n' + text
        return text

    def _actions(self, agent, event):
        if event.type == 'PEER':
            return ['done'] if event.metadata.get('reply') else ['message', 'done']
        if event.type == 'TASK':
            return ['tool', 'message', 'result']
        self.state.require_supervisor(agent.name)
        return {'PLAN': ['tool', 'message', 'plan'],
                'REVIEW': ['tool', 'message', 'review', 'replan'],
                'PROJECT': ['tool', 'message', 'replan', 'finish']}[event.type]

    async def run(self):
        ui.start(self.project, self.workspace.root, self.verbose)
        for agent in self.project.agents:
            agent.server = self.server
            agent.show_thinking = self.show_thinking
            self.hub.register(agent.name)
        try:
            self.workers = [asyncio.create_task(self._agent_loop(agent)) for agent in self.project.agents]
            await self.hub.send(Message('__runtime__', self.state.supervisor, 'PLAN', self._context('plan')))
            await self.finished.wait()
            if self.error:
                raise self.error
            return self.project_accepted
        finally:
            for worker in self.workers:
                worker.cancel()
            await asyncio.gather(*self.workers, return_exceptions=True)
            await asyncio.to_thread(self.workspace.stop_servers)

    async def _agent_loop(self, agent):
        while True:
            event = await self.hub.receive(agent.name)
            ui.activity(agent, event)
            try:
                await agent.work(self.project, event, self._actions(agent, event), self._apply)
            except Exception as error:
                self.workspace.record(agent.name, {'action': 'error', 'event': event.type},
                                      {'success': False, 'result': str(error)}, event.task_id)
                if event.type == 'TASK' and self.state.get(event.task_id).status == 'running':
                    await self._submit(agent, event, {'status': 'blocked', 'summary': str(error), 'output': ''},
                                       event.metadata.get('records', []))
                elif event.type != 'PEER':
                    self.error = error
                    self.finished.set()
                else:
                    ui.error(f'Peer processing failed: {error}')

    async def _submit(self, agent, event, result, records):
        tool_ids = [record['id'] for record in records]
        self.state.submit(agent.name, event.task_id, result, tool_ids)
        task = self.state.get(event.task_id)
        record = self.workspace.record(agent.name, {'action': 'result', **result},
                                       {'success': True, 'result': result}, task.id)
        task.evidence.append(record['id'])
        artifact = self.workspace.task_report(task)
        self.pending_reviews.add(task.id)
        ui.task_result(task, artifact)
        await self.hub.send(Message('__runtime__', self.state.supervisor, 'REVIEW',
                                   self._context('review', task, self._evidence(task.evidence)), task.id))
        await self._schedule()

    async def _apply(self, agent, step, event, reads, records):
        action = step['action']
        if action not in self._actions(agent, event):
            raise ValueError('Action is not permitted for this event.')
        if action in {'plan', 'replan'}:
            for definition in step['tasks']:
                for path in definition.get('artifacts', []):
                    self.workspace.path(path)
        if action == 'tool':
            call = {key: value for key, value in step.items() if key != 'action'}
            outcome = await asyncio.to_thread(self.workspace.execute, call, reads, review=event.type != 'TASK')
            record = self.workspace.record(agent.name, step, outcome, event.task_id)
            records.append(record)
            ui.tool_result(agent.name, step, outcome)
            return False, record
        if action == 'message':
            if step['recipient'] == agent.name:
                raise ValueError('Do not message yourself.')
            await self.hub.send(Message(agent.name, step['recipient'], 'PEER', step['content'],
                                        event.task_id, {'reply': event.type == 'PEER'}))
        elif action == 'done':
            return True, None
        elif action == 'plan':
            self.state.plan(agent.name, step['tasks'], initial=True)
            ui.plan(self.state.tasks)
        elif action == 'result':
            if step['status'] == 'completed':
                self.workspace.require_artifacts(self.state.get(event.task_id).artifacts)
            if step['status'] == 'completed' and records and not records[-1]['success']:
                raise ValueError('Investigate or resolve the last tool failure, or report blocked.')
            result = {key: step[key] for key in ['status', 'summary', 'output']}
            await self._submit(agent, event, result, records)
            return True, None
        elif action == 'review':
            if event.task_id not in self.pending_reviews:
                raise ValueError('No review is pending for the current task.')
            task = self.state.get(event.task_id)
            evidence = self._evidence([*task.evidence, *[record['id'] for record in records]])
            if step['verdict'] == 'accept':
                self.workspace.require_artifacts(task.artifacts)
            self.state.review(agent.name, task.id, step['verdict'], step['reason'], step['evidence'], evidence)
            self.pending_reviews.remove(task.id)
            self.workspace.task_report(task)
            ui.review(task, 'blocked' if self.state.stop_reason else step['verdict'], task.feedback)
        elif action == 'replan':
            self.state.plan(agent.name, step['tasks'])
            for definition in step['tasks']:
                self.state.get(definition['id']).feedback = step['reason']
            self.final_queued = False
            if event.type == 'REVIEW':
                task = self.state.get(event.task_id)
                if task.status in {'review', 'blocked'}:
                    record = self.workspace.record(agent.name, step, {'success': True, 'result': 'Plan updated.'}, event.task_id)
                    return False, {'record': record, 'remaining': f'Decide the current task {task.id} with review.'}
                self.pending_reviews.discard(task.id)
        elif action == 'finish':
            self.state.require_supervisor(agent.name)
            if not isinstance(step['accepted'], bool) or not step['reason'].strip():
                raise ValueError('Explicitly accept or reject the project and explain why.')
            if step['accepted']:
                if self.pending_reviews or any(task.status not in {'completed', 'cancelled'} for task in self.state.tasks):
                    raise ValueError('Unresolved tasks prevent project acceptance.')
                for task in self.state.tasks:
                    if task.status == 'completed':
                        self.workspace.require_artifacts(task.artifacts)
                ids = [ref for task in self.state.tasks if task.status == 'completed' for ref in task.evidence]
                current = [record['id'] for record in records]
                self.state.check_evidence(step['reason'], step['evidence'], self._evidence([*ids, *current]))
                if self.workspace.list_files() != '(directory is empty)':
                    fresh = {record['id'] for record in records if record['success']
                             and record['request'].get('tool') in {'read', 'run', 'browser_check'}}
                    if not fresh.intersection(step['evidence']):
                        raise ValueError('Inspect the current artifacts and cite fresh evidence before final approval.')
            self.project_accepted = step['accepted']
            record = self.workspace.record(agent.name, step, {'success': True, 'result': step['reason']})
            (self.workspace.audit / 'project-review.json').write_text(json.dumps(record, indent=2))
            ui.finish(self.project_accepted, step['reason'])
            self.finished.set()
            return True, None
        else:
            raise ValueError(f'Unknown action: {action}')
        record = self.workspace.record(agent.name, step, {'success': True, 'result': 'Action applied.'}, event.task_id)
        if action == 'message':
            return event.type == 'PEER', record
        if self.state.stop_reason:
            ui.finish(False, self.state.stop_reason)
            self.finished.set()
        else:
            await self._schedule()
        return True, record

    async def _schedule(self):
        if self.finished.is_set():
            return
        for task in self.state.ready():
            self.state.start(task.id)
            await self.hub.send(Message(self.state.supervisor, task.owner, 'TASK',
                                       self._context('task', task), task.id))
        if not self.state.planned or self.pending_reviews or any(task.status == 'running' for task in self.state.tasks):
            return
        if any(task.status in {'pending', 'blocked'} for task in self.state.tasks):
            ui.finish(False, 'Blocked or unrunnable tasks remain.')
            self.finished.set()
        elif not self.final_queued:
            self.final_queued = True
            ids = [ref for task in self.state.tasks for ref in task.evidence]
            await self.hub.send(Message('__runtime__', self.state.supervisor, 'PROJECT',
                                       self._context('project_ready', records=self._evidence(ids))))
