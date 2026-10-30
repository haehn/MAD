"""Task graph invariants and atomic Supervisor decisions. No I/O or model calls."""
import copy
from mad.task import Task


class ProjectState:
    def __init__(self, project):
        self.project = project
        self.planned = False
        self.evidence = {}

    @property
    def tasks(self):
        return self.project.tasks

    @property
    def completed(self):
        return {task.id for task in self.tasks if task.status == "completed"}

    @property
    def running(self):
        return {task.id for task in self.tasks if task.status == "running"}

    def install_plan(self, data):
        tasks = [Task.from_dict(item) for item in data["tasks"]]
        if any(task.status != "pending" for task in tasks):
            raise ValueError("New tasks must start pending.")
        self._validate_tasks(tasks)
        self.project.tasks = tasks
        self.planned = True

    def ready(self):
        completed = self.completed
        return [task for task in self.tasks if task.status == "pending"
                and all(dep in completed for dep in task.dependencies)]

    def start(self, task):
        if task not in self.ready():
            raise ValueError(f"Task is not ready: {task.id}")
        task.status = "running"

    def record_result(self, task_id, sender, status):
        task = self._get_task(task_id)
        if task.owner != sender or task.status != "running":
            raise ValueError(f"Unexpected result for {task_id} from {sender}")
        task.status = "review" if status == "completed" else "blocked"
        return task

    def decide(self, actions, reviewed_id):
        proposed = copy.deepcopy(self.tasks)
        if not self._apply_replan_actions(actions, proposed):
            raise ValueError("Invalid Supervisor action; task graph unchanged.")
        self._validate_tasks(proposed)
        reviewed = self._find_task_in(proposed, reviewed_id)
        if reviewed and reviewed.status == "review":
            raise ValueError("Supervisor must explicitly resolve the reviewed task.")
        self.project.tasks = proposed

    def finished(self, pending_reviews):
        return (self.planned and not pending_reviews and not self.running
                and not any(task.status == "review" for task in self.tasks)
                and not self.ready())

    @staticmethod
    def validate_assessments(criteria, assessments, evidence):
        if not isinstance(assessments, list) or len(assessments) != len(set(criteria)):
            raise ValueError("Acceptance requires an assessment for every criterion.")
        seen = set()
        for item in assessments:
            if not isinstance(item, dict):
                raise ValueError("Invalid criterion assessment.")
            criterion = item.get('criterion')
            if not isinstance(criterion, str) or criterion not in criteria or criterion in seen:
                raise ValueError("Assessment must name each exact criterion once.")
            seen.add(criterion)
            if not isinstance(item.get('reason'), str) or not item['reason'].strip():
                raise ValueError("Acceptance requires an explanation for each criterion.")
            refs = item.get('evidence')
            if not isinstance(refs, list) or not refs or any(
                not isinstance(ref, str) or ref not in evidence for ref in refs
            ):
                raise ValueError("Acceptance must cite supplied evidence IDs.")

    def _apply_replan_actions(self, actions, tasks):
        planned_add_ids = set()
        for action in actions:
            if action.get('type') != 'add':
                continue
            task_data = action.get('task')
            if not task_data:
                return False
            task_id = task_data.get('id')
            if not task_id:
                return False
            planned_add_ids.add(task_id)
        for action in actions:
            if not self._apply_replan_action(action, tasks, planned_add_ids):
                return False
        return True

    def _apply_replan_action(self, action, tasks, planned_add_ids):
        action_type = action.get('type')
        if action_type == 'accept':
            return self._action_accept(action, tasks)
        if action_type == 'add':
            return self._action_add(action, tasks)
        if action_type == 'revise':
            return self._action_revise(action, tasks, planned_add_ids)
        if action_type == 'reassign':
            return self._action_reassign(action, tasks)
        if action_type == 'cancel':
            return self._action_cancel(action, tasks)
        if action_type == 'reopen':
            return self._action_reopen(action, tasks)
        return False

    def _action_accept(self, action, tasks):
        task_id = action.get('task_id')
        task = self._find_task_in(tasks, task_id)
        if not task:
            return False
        if task.status != 'review':
            return False
        self.validate_assessments(task.review_criteria, action.get('assessments'),
                                  self.evidence.get(task.id, {}))
        task.status = 'completed'
        return True

    def _action_add(self, action, tasks):
        task_data = action.get('task')
        if not task_data:
            return False
        try:
            task = Task.from_dict(task_data)
        except (KeyError, TypeError, ValueError):
            return False
        if self._find_task_in(tasks, task.id):
            return False
        if task.status != "pending":
            return False
        tasks.append(task)
        return True

    def _action_revise(self, action, tasks, planned_add_ids):
        task_id = action.get('task_id')
        task = self._find_task_in(tasks, task_id)
        if not task:
            return False
        if task.status == 'running':
            return False
        updates = action.get('updates', {})
        if 'dependencies' in updates:
            existing = {item.id for item in tasks}
            allowed = existing | planned_add_ids
            for dependency in updates['dependencies']:
                if dependency not in allowed:
                    return False
        for field in ['title', 'description', 'owner', 'dependencies', 'acceptance']:
            if field in updates:
                setattr(task, field, updates[field])
        task.status = 'pending'
        return True

    def _action_reassign(self, action, tasks):
        task_id = action.get('task_id')
        owner = action.get('owner')
        task = self._find_task_in(tasks, task_id)
        if not task:
            return False
        if not owner:
            return False
        valid_agents = {agent.name for agent in self.project.agents}
        if owner not in valid_agents:
            return False
        if task.status == 'running':
            return False
        if task.owner == owner:
            return False
        task.owner = owner
        task.status = 'pending'
        return True

    def _action_cancel(self, action, tasks):
        task_id = action.get('task_id')
        task = self._find_task_in(tasks, task_id)
        if not task:
            return False
        if task.status == 'running':
            return False
        task.status = 'cancelled'
        return True

    def _action_reopen(self, action, tasks):
        task_id = action.get('task_id')
        task = self._find_task_in(tasks, task_id)
        if not task:
            return False
        if task.status == 'running':
            return False
        task.status = 'pending'
        return True

    def _validate_tasks(self, tasks):
        agent_names = {agent.name for agent in self.project.agents}
        task_ids = [task.id for task in tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError('Duplicate task IDs.')
        task_id_set = set(task_ids)
        for task in tasks:
            if not isinstance(task.id, str) or not task.id.strip():
                raise ValueError("Task IDs must be nonempty strings.")
            if not isinstance(task.title, str) or not task.title.strip():
                raise ValueError("Task titles must be nonempty strings.")
            if not isinstance(task.dependencies, list) or not all(isinstance(dep, str) for dep in task.dependencies):
                raise ValueError(f"{task.id} dependencies must be a list of task IDs.")
            if not isinstance(task.acceptance, list) or not all(isinstance(item, str) for item in task.acceptance):
                raise ValueError(f"{task.id} acceptance must be a list of strings.")
            if task.owner not in agent_names:
                raise ValueError(f'{task.id} has invalid owner {task.owner!r}')
            for dependency in task.dependencies:
                if dependency not in task_id_set:
                    raise ValueError(f'{task.id} has unknown dependency {dependency!r}')
                if dependency == task.id:
                    raise ValueError(f'{task.id} depends on itself.')

        graph = {task.id: task.dependencies for task in tasks}
        visiting, visited = set(), set()

        def visit(task_id):
            if task_id in visiting:
                raise ValueError("Task dependency cycle.")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in graph[task_id]:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in graph:
            visit(task_id)

    def _find_task_in(self, tasks, task_id):
        for task in tasks:
            if task.id == task_id:
                return task
        return None

    def _find_task(self, task_id):
        return self._find_task_in(self.project.tasks, task_id)

    def _get_task(self, task_id):
        task = self._find_task(task_id)
        if task is None:
            raise ValueError(f'Unknown task: {task_id}')
        return task

