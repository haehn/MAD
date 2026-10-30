"""Project configuration, Markdown parsing, and task state enforcement."""
import copy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from mad.agent import Agent


@dataclass
class Task:
    id: str
    title: str
    description: str = ''
    owner: str | None = None
    dependencies: list[str] = field(default_factory=list)
    acceptance: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    status: str = 'pending'
    result: dict = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)
    feedback: str = ''

    @classmethod
    def from_dict(cls, data):
        fields = {'id', 'title', 'description', 'owner', 'dependencies', 'acceptance', 'artifacts', 'status'}
        if not isinstance(data, dict) or set(data) - fields:
            raise ValueError('Task must contain only task definition fields.')
        return cls(**data)

    def to_dict(self):
        return asdict(self)


@dataclass
class Project:
    objective: str = ''
    workspace: str = ''
    success: list[str] = field(default_factory=list)
    agents: list[Agent] = field(default_factory=list)
    tasks: list = field(default_factory=list)

    @classmethod
    def from_file(cls, path):
        sections = {'objective': [], 'workspace': [], 'success': [], 'agents': []}
        section = 'objective'
        for raw in Path(path).read_text(encoding='utf-8').splitlines():
            line = raw.strip()
            if not line:
                continue
            header = line[:-1].lower() if line.endswith(':') else None
            if header in {'workspace', 'success', 'agents'}:
                section = header
            else:
                sections[section].append(line)
        project = cls(
            objective=' '.join(sections['objective']),
            workspace=' '.join(sections['workspace']),
            success=[line.removeprefix('-').strip() for line in sections['success']],
            agents=[Agent.from_definition(line) for line in sections['agents']],
        )
        if not project.objective or not project.workspace or not project.agents:
            raise ValueError('Objective, WORKSPACE, and AGENTS are required.')
        return project


class ProjectState:
    def __init__(self, project):
        self.project = project
        names = [agent.name for agent in project.agents]
        if len(names) != len(set(names)) or '__runtime__' in names:
            raise ValueError('Agent names must be unique and cannot be __runtime__.')
        supervisors = [agent.name for agent in project.agents if agent.template == 'Supervisor.md']
        if len(supervisors) != 1:
            raise ValueError('Exactly one Supervisor role is required.')
        self.supervisor = supervisors[0]
        self.planned = False
        self.attempts, self.stop_reason = {}, None

    @property
    def tasks(self):
        return self.project.tasks

    def require_supervisor(self, actor):
        if actor != self.supervisor:
            raise ValueError('Only the Supervisor may plan, review, or accept work.')

    def get(self, task_id):
        task = next((task for task in self.tasks if task.id == task_id), None)
        if task is None:
            raise ValueError(f'Unknown task: {task_id}')
        return task

    def plan(self, actor, definitions, initial=False):
        self.require_supervisor(actor)
        if initial and self.planned:
            raise ValueError('Initial plan already installed; use replan.')
        if not initial and not self.planned:
            raise ValueError('An initial plan is required.')
        if not isinstance(definitions, list) or not definitions:
            raise ValueError('Provide at least one task definition.')
        proposed = [] if initial else copy.deepcopy(self.tasks)
        supplied = set()
        for definition in definitions:
            task = Task.from_dict(definition)
            if not isinstance(task.id, str) or not task.id or task.id in supplied:
                raise ValueError('Task IDs must be unique nonempty strings.')
            supplied.add(task.id)
            if task.status not in ({'pending'} if initial else {'pending', 'cancelled'}):
                raise ValueError('Planned tasks start pending or are explicitly cancelled.')
            old = next((item for item in proposed if item.id == task.id), None)
            if task.status == 'pending' and self.attempts.get(task.id, 0) >= 4:
                raise ValueError(f'Task {task.id} exhausted its three rework retries.')
            if old and old.status == 'running':
                raise ValueError(f'Cannot edit running task {task.id}.')
            if old:
                proposed[proposed.index(old)] = task
            else:
                proposed.append(task)
        self.validate(proposed)
        if not initial and [task.to_dict() for task in proposed] == [task.to_dict() for task in self.tasks]:
            raise ValueError('Replan made no change; decide the review or finish instead.')
        self.project.tasks = proposed
        self.planned = True

    def validate(self, tasks):
        names = {agent.name for agent in self.project.agents}
        graph = {task.id: task.dependencies for task in tasks}
        if len(graph) != len(tasks):
            raise ValueError('Duplicate task IDs.')
        for task in tasks:
            if task.owner not in names or not isinstance(task.title, str) or not task.title.strip():
                raise ValueError(f'Invalid owner or title for {task.id}.')
            if not isinstance(task.description, str):
                raise ValueError('Descriptions must be text.')
            for values in [task.dependencies, task.acceptance, task.artifacts]:
                if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
                    raise ValueError('Dependencies, acceptance, and artifacts must be lists of strings.')
            if any(dependency not in graph for dependency in task.dependencies):
                raise ValueError(f'Unknown dependency for {task.id}.')
        active, visited = set(), set()

        def visit(task_id):
            if task_id in active:
                raise ValueError('Task dependency cycle.')
            if task_id in visited:
                return
            active.add(task_id)
            for dependency in graph[task_id]:
                visit(dependency)
            active.remove(task_id)
            visited.add(task_id)

        for task_id in graph:
            visit(task_id)

    def ready(self):
        completed = {task.id for task in self.tasks if task.status == 'completed'}
        return [task for task in self.tasks if task.status == 'pending'
                and all(dependency in completed for dependency in task.dependencies)]

    def start(self, task_id):
        task = self.get(task_id)
        if task not in self.ready():
            raise ValueError(f'Task is not ready: {task_id}')
        self.attempts[task_id] = self.attempts.get(task_id, 0) + 1
        task.status = 'running'

    def submit(self, actor, task_id, result, evidence):
        task = self.get(task_id)
        if task.owner != actor or task.status != 'running':
            raise ValueError(f'Unexpected result for {task_id} from {actor}.')
        if result.get('status') not in {'completed', 'blocked'}:
            raise ValueError('Worker results are completed claims or blocked reports.')
        task.result, task.evidence = dict(result), list(evidence)
        task.feedback = ''
        task.status = 'review' if result['status'] == 'completed' else 'blocked'

    @staticmethod
    def check_evidence(reason, references, evidence):
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('Explain the decision.')
        if not isinstance(references, list) or not references or any(
            not isinstance(ref, str) or ref not in evidence for ref in references
        ):
            raise ValueError('Cite recorded evidence IDs, not invented checks.')

    def review(self, actor, task_id, verdict, reason, references, evidence):
        self.require_supervisor(actor)
        task = self.get(task_id)
        if task.status not in {'review', 'blocked'}:
            raise ValueError('The current task is not awaiting a review decision.')
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('Explain the decision.')
        if verdict == 'accept':
            if task.status != 'review':
                raise ValueError('A blocked task cannot be accepted.')
            self.check_evidence(reason, references, evidence)
            task.status = 'completed'
        elif verdict in {'rework', 'blocked'}:
            task.status = 'pending' if verdict == 'rework' else 'blocked'
            if verdict == 'rework' and self.attempts.get(task_id, 0) >= 4:
                task.status = 'blocked'
                self.stop_reason = f'Task {task_id} exhausted its three rework retries; project not accepted.'
                reason = f'{self.stop_reason}\n\nLatest Supervisor feedback: {reason}'
        else:
            raise ValueError('Review verdict must be accept, rework, or blocked.')
        task.feedback = reason
