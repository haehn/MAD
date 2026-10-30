import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mad.project import Project
from mad.runtime import Runtime, parse_json_response
from mad.task import Task
from mad.workspace import Workspace
from mad.message import Message
from mad.message_hub import MessageHub


class BaselineTests(unittest.TestCase):
    def test_all_examples_parse(self):
        for path in Path('examples').glob('*.md'):
            with self.subTest(path=path):
                project = Project.from_file(path)
                self.assertTrue(project.objective)
                self.assertTrue(project.workspace)
                self.assertEqual(sum(a.template == 'Supervisor.md' for a in project.agents), 1)
                self.assertTrue(all(a.instructions and a.skills for a in project.agents))

    def test_fenced_json(self):
        self.assertEqual(parse_json_response('```json\n{"tasks": []}\n```'), {'tasks': []})

    def test_workspace_and_audit(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Workspace(root)
            workspace.write('result.txt', 'deliverable')
            workspace.append('result.txt', '!')
            self.assertEqual(workspace.read('result.txt'), 'deliverable!')
            audit = workspace.write_task_result(Task('T1', 'Work'), 'Worker', 'review', 'Summary', 'Output')
            self.assertEqual(str(audit), '.mad/audit/T1.md')
            self.assertNotIn('.mad', workspace.list_files())
            for path in ['../outside', '.mad/audit/T1.md']:
                with self.assertRaises(ValueError):
                    workspace.read(path)


class AsyncBaselineTests(unittest.IsolatedAsyncioTestCase):
    async def test_hub_delivery_and_history(self):
        hub = MessageHub()
        hub.register('Worker')
        message = Message('Supervisor', 'Worker', 'TASK')
        await hub.send(message)
        self.assertIs(await hub.receive('Worker'), message)
        self.assertEqual(hub.history(), [message])
        with self.assertRaises(ValueError):
            await hub.send(Message('Worker', 'Missing', 'PEER'))

    async def test_worker_completion_requires_review(self):
        with tempfile.TemporaryDirectory() as root:
            supervisor = SimpleNamespace(name='Supervisor', template='Supervisor.md')
            worker = SimpleNamespace(name='Worker', template='Worker.md')
            project = Project('Goal', root, agents=[supervisor, worker])
            runtime = Runtime(project)
            runtime.supervisor = supervisor
            runtime.hub.register('Supervisor')
            project.tasks = [Task('T1', 'Work', owner='Worker', status='running')]
            await runtime._handle_task_result(Message('Worker', '__runtime__', 'TASK_RESULT', task_id='T1', metadata={'status': 'completed'}))
            self.assertEqual(project.tasks[0].status, 'review')
            self.assertFalse(runtime._project_finished())
            self.assertEqual((await runtime.hub.receive('Supervisor')).type, 'REPLAN_REQUEST')
