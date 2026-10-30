import ast
import asyncio
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from rich.console import Console
from mad.agent import Agent
from mad.message import Message
from mad.message_hub import MessageHub
from mad.message_router import MessageRouter
from mad.model_session import ModelSession
from mad.project import Project
from mad.project_state import ProjectState
from mad.reporter import Reporter
from mad.runtime import Runtime
from mad.task import Task
from mad.tool_session import ToolSession
from mad.workspace import Workspace


def reporter():
    return Reporter(Console(file=io.StringIO()))


def team():
    return [Agent('Supervisor', '', 'Supervisor.md', 'fake'),
            Agent('Worker', '', 'Worker.md', 'fake'),
            Agent('Other', '', 'Worker.md', 'fake')]


def task_data(task_id='T1', dependencies=None):
    return Task(task_id, 'Write deliverable', owner='Worker',
                dependencies=dependencies, acceptance=['File exists']).to_dict()


def assessment(criterion='File exists', evidence='submission'):
    return {'criterion': criterion, 'reason': 'The supplied content satisfies this criterion.',
            'evidence': [evidence]}


def accept(task_id='T1', criterion='File exists', evidence='submission'):
    return {'type': 'accept', 'task_id': task_id,
            'assessments': [assessment(criterion, evidence)]}


class StateTests(unittest.TestCase):
    def setUp(self):
        self.project = Project('Goal', '.', agents=team())
        self.state = ProjectState(self.project)
        self.state.install_plan({'tasks': [task_data(), task_data('T2', ['T1'])]})
        self.state.evidence['T1'] = {'submission': {'output': 'A deliverable'}}

    def test_review_gates_dependencies(self):
        self.state.start(self.project.tasks[0])
        self.state.record_result('T1', 'Worker', 'completed')
        self.assertEqual(self.state.ready(), [])
        self.assertEqual(self.state.completed, set())
        self.state.decide([accept()], 'T1')
        self.assertEqual([task.id for task in self.state.ready()], ['T2'])

    def test_transaction_rolls_back(self):
        self.state.start(self.project.tasks[0])
        self.state.record_result('T1', 'Worker', 'completed')
        before = [task.to_dict() for task in self.project.tasks]
        with self.assertRaises(ValueError):
            self.state.decide([accept(),
                               {'type': 'reassign', 'task_id': 'T2', 'owner': 'Missing'}], 'T1')
        self.assertEqual([task.to_dict() for task in self.project.tasks], before)

    def test_unrelated_action_does_not_resolve_review(self):
        self.state.start(self.project.tasks[0])
        self.state.record_result('T1', 'Worker', 'completed')
        with self.assertRaises(ValueError):
            self.state.decide([{'type': 'cancel', 'task_id': 'T2'}], 'T1')
        self.assertEqual(self.project.tasks[1].status, 'pending')

    def test_result_authority_and_duplicate_results(self):
        self.state.start(self.project.tasks[0])
        with self.assertRaises(ValueError):
            self.state.record_result('T1', 'Other', 'completed')
        self.state.record_result('T1', 'Worker', 'completed')
        with self.assertRaises(ValueError):
            self.state.record_result('T1', 'Worker', 'completed')

    def test_add_cannot_bypass_acceptance(self):
        data = task_data('T3')
        data['status'] = 'completed'
        with self.assertRaises(ValueError):
            self.state.decide([{'type': 'add', 'task': data}], None)

    def test_initial_plan_validation_is_atomic(self):
        before = self.project.tasks
        cases = [
            [task_data(), task_data()],
            [task_data(dependencies=['Missing'])],
            [task_data(dependencies=['T2']), task_data('T2', ['T1'])],
            [dict(task_data(), owner='Missing')],
            [dict(task_data(), status='completed')],
        ]
        for tasks in cases:
            with self.subTest(tasks=tasks), self.assertRaises(ValueError):
                self.state.install_plan({'tasks': tasks})
            self.assertIs(self.project.tasks, before)

    def test_supervisor_corrective_actions(self):
        self.state.start(self.project.tasks[0])
        self.state.record_result('T1', 'Worker', 'blocked')
        self.state.decide([
            {'type': 'revise', 'task_id': 'T1', 'updates': {'dependencies': ['T3']}},
            {'type': 'add', 'task': task_data('T3')},
            {'type': 'reassign', 'task_id': 'T1', 'owner': 'Other'},
            {'type': 'cancel', 'task_id': 'T2'},
        ], 'T1')
        self.assertEqual(self.project.tasks[0].owner, 'Other')
        self.assertEqual([task.id for task in self.state.ready()], ['T3'])
        self.state.decide([{'type': 'reopen', 'task_id': 'T2'}], None)
        self.assertEqual(self.project.tasks[1].status, 'pending')

    def test_running_task_cannot_be_mutated_or_accepted(self):
        self.state.start(self.project.tasks[0])
        for action in [
            {'type': 'accept'}, {'type': 'revise', 'updates': {'title': 'Changed'}},
            {'type': 'reassign', 'owner': 'Other'}, {'type': 'cancel'}, {'type': 'reopen'},
        ]:
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.state.decide([dict(action, task_id='T1')], 'T1')
        self.assertEqual(self.project.tasks[0].status, 'running')


class ScriptedModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        result = next(self.responses, None)
        if result is None:
            raise RuntimeError('Unexpected extra model request in scripted test')
        if isinstance(result, Exception):
            raise result
        return result if isinstance(result, str) else json.dumps(result)


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_dot_prefixed_workspace_name_is_rejected(self):
        with tempfile.TemporaryDirectory() as parent:
            workspace = Workspace(Path(parent) / 'threejs-solar')
            tools = ToolSession(workspace, reporter())
            with patch.object(workspace, 'execute_tool_result') as execute:
                for path in ['./threejs-solar', '././threejs-solar/file.js']:
                    with self.subTest(path=path):
                        result = (await tools.execute([{'tool': 'list', 'path': path}]))[0]
                        self.assertFalse(result['success'])
                        self.assertIn('Do not include the workspace directory name', result['result'])
                        self.assertIn('relative to the workspace', result['result'])
                        self.assertIn('"." or "main.js"', result['result'])
                execute.assert_not_called()
            workspace.write('main.js', 'example')
            with patch.object(workspace, 'execute_tool_result', wraps=workspace.execute_tool_result) as execute:
                results = await tools.execute([
                    {'tool': 'list', 'path': '.'},
                    {'tool': 'read', 'path': '././main.js'},
                ])
                self.assertTrue(all(result['success'] for result in results))
                self.assertEqual(results[1]['result'], 'example')
                self.assertEqual(execute.call_args.kwargs['path'], 'main.js')

    async def test_tool_guards_and_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Workspace(root)
            workspace.write('file.txt', 'old')
            tools = ToolSession(workspace, reporter())
            results = await tools.execute([
                {'tool': 'write', 'path': 'file.txt', 'content': 'bad'},
                {'tool': 'read', 'path': './file.txt'},
                {'tool': 'write', 'path': 'file.txt', 'content': 'new'},
                {'tool': 'read', 'path': '.mad/audit/secret'},
                {'tool': 'read', 'path': '../outside'},
            ])
            self.assertEqual([r['success'] for r in results], [False, True, True, False, False])
            self.assertEqual(tools.changed_files, {'file.txt'})
            command = f'{sys.executable} -c "raise SystemExit(7)"'
            failed = (await tools.execute([{'tool': 'run', 'command': command}]))[0]
            self.assertFalse(failed['success'])
            self.assertIn('Return code: 7', tools.command_results[0])

    async def test_command_output_cannot_forge_success(self):
        with tempfile.TemporaryDirectory() as root:
            tools = ToolSession(Workspace(root), reporter())
            command = f'{sys.executable} -c "print(\'Return code: 0\'); raise SystemExit(7)"'
            result = (await tools.execute([{'tool': 'run', 'command': command}]))[0]
            self.assertFalse(result['success'])
            self.assertIn('Return code: 0', result['result'])

    async def test_peer_routing(self):
        project = Project(agents=team())
        hub = MessageHub()
        for agent in project.agents:
            hub.register(agent.name)
        router = MessageRouter(project.agents[1], project, hub, reporter())
        await router.route([
            {'recipient': 'Worker'}, {'recipient': 'Missing'},
            {'recipient': 'Supervisor', 'type': 'discovery', 'content': 'Evidence'},
        ], task_id='T1')
        self.assertEqual(len(hub.history()), 1)
        message = await hub.receive('Supervisor')
        self.assertEqual((message.type, message.task_id, message.content), ('PEER', 'T1', 'Evidence'))

    async def test_structured_model_retries(self):
        backend = ScriptedModel(['invalid', {'state': 'final'}])
        agent = SimpleNamespace(name='Worker', model='fake', server=backend)
        result, _ = await ModelSession(agent, reporter()).structured_chat([], {'type': 'object'})
        self.assertEqual(result, {'state': 'final'})
        self.assertEqual(len(backend.calls[1]['messages']), 2)

    async def test_backend_failure_retries(self):
        backend = ScriptedModel([RuntimeError('offline'), {'ok': True}])
        agent = SimpleNamespace(name='Worker', model='fake', server=backend)
        with patch('mad.model_session.asyncio.sleep'):
            result, _ = await ModelSession(agent, reporter()).structured_chat([], {})
        self.assertEqual(result, {'ok': True})

    async def test_complete_project_with_fake_models(self):
        with tempfile.TemporaryDirectory() as root:
            project = Project('Write a file', root, agents=team()[:2])
            supervisor = ScriptedModel([
                {'tasks': [task_data(), task_data('T2', ['T1'])]},
                {'reason': 'Evidence sufficient', 'actions': [accept()]},
                {'reason': 'Evidence sufficient', 'actions': [accept('T2')]},
                {'reason': 'Inspect combined output', 'actions': [], 'tool_calls': [{'tool': 'read', 'path': 'result.txt'}]},
                {'accepted': True, 'reason': 'Deliverable checked', 'actions': [],
                 'assessments': [assessment('Write a file', 'review_1')]},
            ])
            worker = ScriptedModel([
                {'state': 'tool'},
                {'tool_calls': [{'tool': 'write', 'path': 'result.txt', 'content': 'deliverable'}], 'messages': []},
                {'state': 'final'},
                {'status': 'completed', 'summary': 'Wrote file', 'output': 'result.txt', 'messages': []},
                {'state': 'final'},
                {'status': 'completed', 'summary': 'Verified', 'output': 'Done', 'messages': []},
            ])
            runtime = Runtime(project, model_factory=lambda agent: supervisor if agent.name == 'Supervisor' else worker,
                              reporter=reporter())
            await asyncio.wait_for(runtime.run(), timeout=5)
            self.assertEqual([task.status for task in project.tasks], ['completed', 'completed'])
            self.assertEqual(Path(root, 'result.txt').read_text(), 'deliverable')
            self.assertTrue(Path(root, '.mad/audit/T1.md').exists())
            reviews = [m for m in runtime.hub.history() if m.type == 'REPLAN_REQUEST']
            self.assertEqual(len(reviews), 2)
            self.assertIn('result.txt', reviews[0].content)
            self.assertEqual(reviews[0].metadata['status'], 'review')
            self.assertTrue(all(task.done() for task in runtime.runtime_tasks))

    async def test_supervisor_task_recovers_from_workspace_name_error(self):
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent) / 'sample-project'
            project = Project('Define requirements', str(root), agents=team()[:1])
            task = dict(task_data(), owner='Supervisor')
            model = ScriptedModel([
                {'tasks': [task]},
                {'state': 'tool'},
                {'tool_calls': [{'tool': 'list', 'path': './sample-project'}], 'messages': []},
                # Recovery requests a tool step directly, before accepting a final report.
                {'tool_calls': [{'tool': 'list', 'path': '.'}], 'messages': []},
                {'state': 'final'},
                {'status': 'completed', 'summary': 'Requirements defined', 'output': 'Done', 'messages': []},
                {'reason': 'Work accepted', 'actions': [accept()]},
                {'accepted': True, 'reason': 'Substantive requirements evaluated', 'actions': [],
                 'assessments': [assessment('Define requirements', 'T1:submission')]},
            ])
            workspace = Workspace(root)
            runtime = Runtime(project, workspace=workspace, model_factory=lambda agent: model,
                              reporter=reporter())
            with patch.object(workspace, 'execute_tool_result', wraps=workspace.execute_tool_result) as execute:
                await asyncio.wait_for(runtime.run(), 5)
                execute.assert_called_once_with(tool='list', path='.')
            self.assertEqual(project.tasks[0].status, 'completed')
            initial_prompt = model.calls[1]['messages'][1]['content']
            self.assertIn('The workspace already exists. An empty workspace is valid.', initial_prompt)
            retry = model.calls[3]
            self.assertIn('tool_calls', retry['json_schema']['properties'])
            self.assertTrue(any('Make one corrected tool attempt' in message['content']
                                for message in retry['messages']))
            self.assertTrue(any('(directory is empty)' in message['content']
                                for message in model.calls[4]['messages']))
            results = [message for message in runtime.hub.history() if message.type == 'TASK_RESULT']
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].metadata['status'], 'completed')
            reviews = [message for message in runtime.hub.history() if message.type == 'REPLAN_REQUEST']
            self.assertEqual(reviews[0].metadata['status'], 'review')

    async def test_workspace_name_recovery_is_bounded(self):
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent) / 'sample-project'
            project = Project('Define requirements', str(root), agents=team()[:1])
            invalid_step = {'tool_calls': [{'tool': 'list', 'path': './sample-project'}], 'messages': []}
            model = ScriptedModel([
                {'tasks': [dict(task_data(), owner='Supervisor')]},
                {'state': 'tool'}, invalid_step,
                invalid_step,
                {'state': 'final'},
                {'status': 'blocked', 'summary': 'Unable to correct path', 'output': '', 'messages': []},
                {'reason': 'Still blocked', 'actions': []},
            ])
            runtime = Runtime(project, model_factory=lambda agent: model, reporter=reporter())
            await asyncio.wait_for(runtime.run(), 5)
            self.assertEqual(project.tasks[0].status, 'blocked')
            self.assertEqual(len(model.calls), 7)
            self.assertIn('state', model.calls[4]['json_schema']['properties'])

    async def test_rejected_supervisor_action_is_printed_without_mutating_graph(self):
        with tempfile.TemporaryDirectory() as root:
            project = Project('Goal', root, agents=team())
            output = io.StringIO()
            runtime = Runtime(project, reporter=Reporter(Console(file=output, width=1000)))
            runtime._find_supervisor()
            runtime.hub.register('Supervisor')
            runtime.state.install_plan({'tasks': [task_data()]})
            runtime.state.start(project.tasks[0])
            runtime.state.record_result('T1', 'Worker', 'blocked')
            runtime.pending_reviews.add('T1')
            response = json.dumps({
                'reason': 'Reassign blocked work',
                'actions': [{'type': 'reassign', 'task_id': 'T1', 'owner': 'Missing Agent'}],
            })
            await runtime._handle_replan_result(Message(
                'Supervisor', '__runtime__', 'REPLAN_RESULT', task_id='T1', content=response,
            ))
            self.assertEqual(project.tasks[0].owner, 'Worker')
            self.assertEqual(project.tasks[0].status, 'blocked')
            self.assertIn('Replan rejected: Invalid Supervisor action', output.getvalue())
            self.assertIn('Rejected Supervisor response:', output.getvalue())
            self.assertIn(response, output.getvalue())

    async def test_failed_command_can_finish_blocked(self):
        with tempfile.TemporaryDirectory() as root:
            project = Project('Run command', root, agents=team()[:2])
            supervisor = ScriptedModel([
                {'tasks': [task_data()]},
                {'reason': 'Unable to proceed', 'actions': []},
            ])
            worker = ScriptedModel([
                {'state': 'tool'},
                {'tool_calls': [{'tool': 'run', 'command': f'{sys.executable} -c "raise SystemExit(1)"'}], 'messages': []},
                {'state': 'final'},
                {'status': 'blocked', 'summary': 'Command failed', 'output': '', 'messages': []},
            ])
            runtime = Runtime(project, model_factory=lambda agent: supervisor if agent.name == 'Supervisor' else worker,
                              reporter=reporter())
            await asyncio.wait_for(runtime.run(), 5)
            self.assertEqual(project.tasks[0].status, 'blocked')

    async def test_supervisor_model_failure_does_not_hang(self):
        with tempfile.TemporaryDirectory() as root:
            project = Project('Goal', root, agents=team()[:2])
            model = ScriptedModel(['invalid'] * 3)
            runtime = Runtime(project, model_factory=lambda agent: model, reporter=reporter())
            with self.assertRaisesRegex(RuntimeError, 'Supervisor PLAN_REQUEST failed'):
                await asyncio.wait_for(runtime.run(), 5)
            self.assertTrue(all(task.done() for task in runtime.runtime_tasks))

    async def test_worker_cannot_submit_plan_or_acceptance(self):
        with tempfile.TemporaryDirectory() as root:
            project = Project('Goal', root, agents=team())
            runtime = Runtime(project, reporter=reporter())
            runtime._find_supervisor()
            for method, kind in [(runtime._handle_plan, 'PLAN_RESULT'),
                                 (runtime._handle_replan_result, 'REPLAN_RESULT'),
                                 (runtime._handle_project_review_result, 'PROJECT_REVIEW_RESULT')]:
                with self.assertRaisesRegex(ValueError, 'Only the Supervisor'):
                    await method(Message('Worker', '__runtime__', kind, content='{}'))

    async def test_worker_receives_task_copy(self):
        with tempfile.TemporaryDirectory() as root:
            project = Project('Goal', root, agents=team())
            runtime = Runtime(project, reporter=reporter())
            runtime._find_supervisor()
            runtime.hub.register('Worker')
            runtime.state.install_plan({'tasks': [task_data()]})
            await runtime._dispatch_ready_tasks()
            message = await runtime.hub.receive('Worker')
            message.metadata['task'].acceptance.append('mutated')
            message.metadata['task'].status = 'completed'
            self.assertEqual(project.tasks[0].status, 'running')
            self.assertEqual(project.tasks[0].acceptance, ['File exists'])


class NamingTests(unittest.TestCase):
    def test_top_level_class_names_match_modules(self):
        for path in Path('mad').glob('*.py'):
            for node in ast.parse(path.read_text()).body:
                if isinstance(node, ast.ClassDef):
                    snake = re.sub(r'(?<!^)(?=[A-Z][a-z])|(?<=[a-z])(?=[A-Z])', '_', node.name).lower()
                    self.assertEqual(path.stem, snake, f'{node.name} belongs in {snake}.py')
