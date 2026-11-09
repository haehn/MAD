from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from threading import Barrier, Lock
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from rich.console import Console

from .contract import Contract, project
from .runtime import Runtime
from .workspace import Workspace


def definition(name='work', owner='Coder A', qa='QA One', files=('result.txt',), depends=()):
    return dict(ID=name, SETTING='Expert for this assignment.', INPUT={'files': [], 'data': {}},
                TASK='Write the requested result.', OUTPUT={'files': list(files), 'data': {}},
                ACCEPTANCE=['The file contains the requested result.'], OWNER=owner, QA=qa, DEPENDS_ON=list(depends))


class ContractTests(unittest.TestCase):
    def test_contract_is_structured_and_owner_cannot_be_qa(self):
        value = definition()
        contract = Contract.parse(value)
        self.assertEqual(contract.definition(), value)
        for header in value:
            self.assertIn('## ' + header, contract.markdown())
        for invalid in ({**value, 'QA': value['OWNER']}, {**value, 'ACCEPTANCE': []},
                        {**value, 'OUTPUT': {'files': [], 'data': {}}}, {**value, 'extra': True}):
            with self.assertRaises(ValueError):
                Contract.parse(invalid)

    def test_markdown_model_inheritance_overrides_and_relative_roles(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / 'Role.md').write_text('# Role\nDo assigned work.')
            source = '''PROJECT:
Write a report.
WORKSPACE:
./report
INTELLIGENCE:
server: http://default/v1
model: small
AGENTS:
- name: Supervisor
  role: Role.md
  model: large
  server: http://large/v1
- name: Writer
  role: Role.md
- name: QA
  role: Role.md
QA:
QA
OUTPUT:
files: [report.txt]
data: {}
ACCEPTANCE:
- The report exists.
'''
            (path / 'project.md').write_text(source)
            root, workspace, agents = project(path / 'project.md', server='http://override/v1')
            self.assertEqual(root.owner, 'Supervisor')
            self.assertEqual(root.qa, 'QA')
            self.assertEqual(agents[0]['model'], 'large')
            self.assertEqual(agents[0]['server'], 'http://large/v1')
            self.assertEqual(agents[1]['model'], 'small')
            self.assertEqual(agents[1]['server'], 'http://override/v1')
            self.assertEqual(agents[1]['role'], str(path / 'Role.md'))
            self.assertEqual(workspace, Path('report').resolve())

    def test_solar_uses_root_contract_roles_and_explicit_qa(self):
        root, _, agents = project(Path(__file__).parent / 'examples/solar.md')
        self.assertEqual(root.outputs['files'], ['index.html', 'README.md'])
        self.assertEqual(root.qa, 'Tester Two')
        self.assertEqual(len([a for a in agents if Path(a['role']).stem == 'Coder']), 2)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.workspace = Workspace(self.directory.name)
        self.contract = Contract.parse(definition())
        self.agent = SimpleNamespace(name='QA One', role='Tester')

    def test_paths_cannot_escape_or_reference_audit(self):
        for path in ('../outside', '/tmp/outside', '.mad/audit/x'):
            with self.assertRaises(ValueError):
                self.workspace.file(path)
        self.assertEqual(self.workspace.file('./result.txt'), self.workspace.path / 'result.txt')

    def test_actual_execution_errors_are_kept_separate_from_messages(self):
        result = self.workspace.execute("print('actual output'); raise ValueError('actual error')", self.agent, self.contract, 'qa')
        self.assertEqual(result['returncode'], 1)
        self.assertIn('actual error', result['stderr'])
        self.assertEqual(self.workspace.evidence[result['id']]['stdout'], 'actual output\n')
        self.assertIn('actual output', (self.workspace.audit / 'events.md').read_text())

    def test_qa_must_cover_conditions_with_real_independent_evidence(self):
        entry = self.workspace.observe('inspection', self.agent, self.contract, 'qa', content='observed')
        check = dict(criterion=1, status='pass', method='inspection', evidence=[entry['id']], details='Observed the output.')
        report = dict(checks=[check], summary='Checked.')
        self.assertEqual(self.workspace.validate_qa(report, self.contract), report)
        for change in ({'evidence': ['invented']}, {'criterion': True}, {'evidence': []}, {'method': 'execution'}):
            with self.assertRaises(ValueError):
                self.workspace.validate_qa({**report, 'checks': [{**check, **change}]}, self.contract)
        worker = self.workspace.observe('inspection', SimpleNamespace(name='Coder A', role='Coder'), self.contract, 'work', content='claim')
        with self.assertRaises(ValueError):
            self.workspace.validate_qa({**report, 'checks': [{**check, 'evidence': [worker['id']]}]}, self.contract)
        failed = self.workspace.execute('raise RuntimeError("failed check")', self.agent, self.contract, 'qa')
        with self.assertRaises(ValueError):
            self.workspace.validate_qa({**report, 'checks': [{**check, 'method': 'execution', 'evidence': [failed['id']]}]}, self.contract)

    def test_timeout_kills_and_reaps_process(self):
        import subprocess
        process = SimpleNamespace(pid=123, returncode=-9)
        process.communicate = unittest.mock.Mock(side_effect=[subprocess.TimeoutExpired('python', 30), ('partial', '')])
        with patch('mad_crewai.workspace.subprocess.Popen', return_value=process), patch('mad_crewai.workspace.os.killpg') as kill:
            result = self.workspace.execute('while True: pass', self.agent, self.contract, 'qa')
        kill.assert_called_once()
        self.assertTrue(result['timed_out'])
        self.assertEqual(process.communicate.call_count, 2)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.roles = Path(__file__).parent / 'roles'
        self.config = [dict(name=name, role=str(self.roles / role), server='http://local/v1', model=model)
                       for name, role, model in [('Supervisor', 'Supervisor.md', 'large'),
                                                ('Coder A', 'Coder.md', 'small'), ('Coder B', 'Coder.md', 'small'),
                                                ('QA One', 'Tester.md', 'qa-model'), ('QA Two', 'Tester.md', 'qa-model')]]
        self.root = Contract.parse(definition('project', 'Supervisor', 'QA Two'))
        self.runtime = Runtime(self.root, self.directory.name, self.config)
        self.events = []
        self.guard = Lock()
        self.callback = self.respond
        case = self

        class FakeAgent:
            def __init__(self, name, role, server, model):
                self.name, self.role, self.server = name, Path(role).stem, server
                self.instructions = Path(role).read_text()
                self.intelligence = SimpleNamespace(model=model)

            def run(self, brief, expected, tools, progress):
                event, _ = json.JSONDecoder().raw_decode(brief.split('\nAssignment:\n', 1)[1])
                with case.guard:
                    case.events.append((self.name, event.get('phase') or event.get('event'), event['contract']['ID'] if 'contract' in event else 'project', event.get('revision', 0)))
                if event.get('event') == 'plan':
                    case.assertEqual(tools, [], 'Planning must not inspect future deliverables.')
                progress(SimpleNamespace(text='step'))
                return json.dumps(case.callback(self, event))

        self.addCleanup(patch.stopall)
        patch('mad_crewai.runtime.Agent', FakeAgent).start()
        patch('mad_crewai.workspace.Workspace.tools', return_value=[]).start()
        self.terminal = io.StringIO()
        patch('mad_crewai.ui.console', Console(file=self.terminal, color_system=None)).start()
        self.plan = [definition()]

    def respond(self, agent, event):
        if event.get('event') == 'plan':
            return dict(contracts=self.plan, reason='Small parallel plan.')
        if event.get('event') == 'review':
            return dict(decision='accept', reason='QA evidence establishes all conditions.')
        contract = self.runtime.root if event['contract']['ID'] == 'project' else self.runtime.contracts[event['contract']['ID']]
        if event['phase'] == 'work':
            for path in contract.outputs['files']:
                self.runtime.workspace.file(path).write_text('result')
            return dict(output=dict(files=contract.outputs['files'], data={k: 'actual value' for k in contract.outputs['data']}))
        checks = []
        for number, _ in enumerate(contract.acceptance, 1):
            evidence = self.runtime.workspace.observe('inspection', agent, contract, 'qa', content='Read actual deliverable.')
            checks.append(dict(criterion=number, status='pass', method='inspection', evidence=[evidence['id']], details='Actual inspected content meets the criterion.'))
        return dict(checks=checks, summary='Independent QA checked the output.')

    def run_project(self):
        unexpected = io.StringIO()
        with redirect_stdout(unexpected), redirect_stderr(unexpected):
            result = self.runtime.run()
        self.assertEqual(unexpected.getvalue(), '')
        return result

    def test_parallel_workers_qa_gates_handoff_and_root_qa(self):
        self.plan = [definition('a', files=('a.txt',)), definition('b', 'Coder B', 'QA Two', ('b.txt',)),
                     definition('combined', files=('result.txt',), depends=('a', 'b'))]
        barrier = Barrier(2)
        original = self.respond

        def callback(agent, event):
            if event.get('phase') == 'work' and event['contract']['ID'] in ('a', 'b'):
                barrier.wait(timeout=3)
            if event.get('phase') == 'work' and event['contract']['ID'] == 'combined':
                self.assertEqual(set(event['contract']['INPUT']['data']['dependencies']), {'a', 'b'})
                self.assertTrue({'a.txt', 'b.txt'} <= set(event['contract']['INPUT']['files']))
                self.assertTrue(all(self.runtime.contracts[n].state == 'accepted' for n in ('a', 'b')))
            if event.get('phase') == 'qa' and event['contract']['ID'] == 'project':
                self.assertTrue(all(c.state == 'accepted' for c in self.runtime.contracts.values()))
            return original(agent, event)

        self.callback = callback
        self.assertTrue(self.run_project())
        self.assertEqual(self.root.state, 'accepted')
        root_checks = [e for e in self.events if e[1:3] == ('qa', 'project')]
        self.assertEqual(len(root_checks), 1)
        saved = (self.runtime.workspace.contracts / 'combined-r1.md').read_text()
        self.assertIn('a.txt', saved)
        self.assertIn('dependencies:', saved)
        self.assertIn('MULTI AGENT DRIVER', self.terminal.getvalue())
        self.assertIn('[Coder]', self.terminal.getvalue())
        self.assertIn('[Tester]', self.terminal.getvalue())
        self.assertIn('combined r1', self.terminal.getvalue())
        self.assertTrue((self.runtime.workspace.audit / 'crew-output.log').exists())

    def test_planning_never_constructs_workspace_tools(self):
        self.runtime.assemble()
        with patch.object(self.runtime.workspace, 'tools', side_effect=AssertionError('Planning called tools')):
            plan = self.runtime.ask(self.runtime.supervisor, self.root, 'supervisor',
                                    {'event': 'plan', 'root': self.root.definition()},
                                    'Return contracts and reason.', self.runtime.parse_plan)
        self.assertEqual(set(plan), {'work'})
        self.assertEqual(set(self.runtime.parse_plan({'contracts': self.plan})), {'work'})

    def test_one_assignment_per_agent_including_qa(self):
        self.plan = [definition('a', files=('a.txt',)), definition('b', files=('result.txt',))]
        active = set()
        original = self.respond
        import time

        def callback(agent, event):
            if 'phase' in event:
                with self.guard:
                    self.assertNotIn(agent.name, active)
                    active.add(agent.name)
                time.sleep(0.02)
                result = original(agent, event)
                with self.guard:
                    active.remove(agent.name)
                return result
            return original(agent, event)

        self.callback = callback
        self.assertTrue(self.run_project())
        self.assertFalse(active)

    def test_failed_qa_requires_revised_contract_and_rechecks(self):
        original = self.respond

        def callback(agent, event):
            if event.get('event') == 'review' and event['contract']['ID'] == 'work' and event['revision'] == 1:
                value = dict(event['contract'])
                value['INPUT'] = {'files': ['result.txt'], 'data': {}}
                value['TASK'] = 'Correct the reported problem.'
                return dict(decision='revise', reason='Fix observed failure.', contract=value)
            result = original(agent, event)
            if event.get('phase') == 'qa' and event['contract']['ID'] == 'work' and event['revision'] == 1:
                result['checks'][0]['status'] = 'fail'
            return result

        self.callback = callback
        self.assertTrue(self.run_project())
        self.assertEqual(self.runtime.contracts['work'].revision, 2)
        self.assertTrue((self.runtime.workspace.contracts / 'work-r1.md').exists())
        self.assertTrue((self.runtime.workspace.contracts / 'work-r2.md').exists())
        self.assertEqual(len([e for e in self.events if e[1:3] == ('qa', 'work')]), 2)

    def test_root_failure_spawns_correction_and_reruns_integration_qa(self):
        original = self.respond
        original_acceptance = list(self.root.acceptance)

        def callback(agent, event):
            if event.get('event') == 'review' and event['contract']['ID'] == 'project' and event['revision'] == 1:
                correction = definition('fix', files=('result.txt',), depends=('work',))
                return dict(decision='revise', reason='Correct integration failure.', contracts=[correction])
            result = original(agent, event)
            if event.get('phase') == 'qa' and event['contract']['ID'] == 'project' and event['revision'] == 1:
                result['checks'][0]['status'] = 'fail'
            return result

        self.callback = callback
        self.assertTrue(self.run_project())
        self.assertEqual(self.root.revision, 2)
        self.assertEqual(self.root.acceptance, original_acceptance)
        self.assertEqual(self.runtime.contracts['fix'].state, 'accepted')
        self.assertEqual(len([e for e in self.events if e[1:3] == ('qa', 'project')]), 2)

    def test_qa_cannot_fix_declared_deliverables(self):
        original = self.respond

        def callback(agent, event):
            if event.get('event') == 'review':
                self.assertIn('QA changed', event['error'])
                return dict(decision='stop', reason='QA violated its scope.')
            result = original(agent, event)
            if event.get('phase') == 'qa':
                self.runtime.workspace.file('result.txt').write_text('QA edited it')
            return result

        self.callback = callback
        self.assertFalse(self.run_project())
        saved_root = (self.runtime.workspace.contracts / 'project-r1.md').read_text()
        self.assertIn('State: blocked', saved_root)

    def test_missing_root_input_is_rejected_before_planning(self):
        self.root.inputs['files'] = ['missing.txt']
        with self.assertRaisesRegex(ValueError, 'Root INPUT file is missing'):
            self.runtime.assemble()

    def test_plan_rejects_cycles_unknown_owners_and_parallel_output_conflicts(self):
        self.runtime.assemble()
        for contracts in ([definition(depends=('unknown',))],
                          [definition(owner='unknown')],
                          [definition('a'), definition('b', owner='Coder B')],
                          [definition(owner='Supervisor')]):
            with self.subTest(contracts=contracts), self.assertRaises(ValueError):
                self.runtime.parse_plan(dict(contracts=contracts, reason='test'))

    def test_existing_input_cannot_be_read_while_an_unordered_contract_writes_it(self):
        self.runtime.assemble()
        self.runtime.workspace.file('source.txt').write_text('existing data')
        producer = definition('produce', files=('source.txt',))
        consumer = definition('consume', owner='Coder B')
        consumer['INPUT']['files'] = ['source.txt']
        with self.assertRaisesRegex(ValueError, 'ordering dependency'):
            self.runtime.parse_plan(dict(contracts=[producer, consumer], reason='Unsafe concurrent read.'))
        consumer['DEPENDS_ON'] = ['produce']
        self.assertEqual(len(self.runtime.parse_plan(dict(contracts=[producer, consumer], reason='Ordered.'))), 2)

    def test_supervisor_cannot_override_failed_or_missing_qa(self):
        self.runtime.assemble()
        contract = Contract.parse(definition())
        contract.state = 'review'
        for report in (None, {'checks': [{'status': 'fail'}]}, {'checks': [{'status': 'unverified'}]}):
            contract.report = report
            with self.assertRaises(ValueError):
                self.runtime.decide(dict(decision='accept', reason='Unsupported claim'), contract)


@unittest.skipUnless(importlib.util.find_spec('crewai'), 'CrewAI is an optional isolated dependency')
class CrewAITests(unittest.TestCase):
    def test_wrapper_keeps_role_and_model_configuration_and_disables_implicit_delegation(self):
        from .agent import Agent
        agent = Agent('Coder One', Path(__file__).parent / 'roles/Coder.md', 'http://localhost:8012/v1', 'small-model')
        self.assertEqual(agent.name, 'Coder One')
        self.assertEqual(agent.role, 'Coder')
        self.assertEqual(agent.intelligence.model, 'small-model')
        self.assertEqual(agent.intelligence.base_url, 'http://localhost:8012/v1')
        self.assertFalse(agent.crewai.allow_delegation)

    def test_failed_model_request_is_not_silently_retried(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Thread
        from .agent import Agent

        calls = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                calls.append(self.rfile.read(int(self.headers['Content-Length'])))
                response = json.dumps({'error': {'message': 'server busy', 'type': 'server_error'}}).encode()
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(response)))
                self.end_headers()
                self.wfile.write(response)

        with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                agent = Agent('Supervisor', Path(__file__).parent / 'roles/Supervisor.md',
                              f'http://127.0.0.1:{server.server_port}/v1', 'fixture')
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    with self.assertRaises(Exception):
                        agent.run('Return a small plan.', 'JSON plan.')
            finally:
                server.shutdown()
                thread.join()
        self.assertEqual(len(calls), 1, 'A failed request must surface without nested hidden retries.')

    def test_failed_read_is_visible_and_saved_in_structured_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(directory)
            agent = SimpleNamespace(name='Supervisor', role='Supervisor')
            contract = Contract.parse(definition('project', 'Supervisor', 'QA One'))
            activities = []
            tool = workspace.tools(agent, contract, 'supervisor', activity=activities.append)[0]
            with self.assertRaisesRegex(ValueError, 'relative'):
                tool.run(path='/home/d/Projects/MAD/crewai-solar/design.md')
            self.assertEqual(activities, ['read_file', 'read_file failed'])
            events = [json.loads(line) for line in (workspace.audit / 'events.jsonl').read_text().splitlines()]
            self.assertEqual([entry['event'] for entry in events], ['tool_started', 'tool_failed'])
            self.assertEqual(events[1]['path'], '/home/d/Projects/MAD/crewai-solar/design.md')
            self.assertFalse(workspace.evidence)

    def test_full_crewai_work_qa_and_root_qa_with_native_tools(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import re
        from threading import Thread

        folder = Path(__file__).parent
        children = [definition('a', files=('a.txt',)), definition('b', 'Coder B', 'QA Two', ('b.txt',))]
        parallel = Barrier(2)
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                calls.append(request)
                prompt = next(m['content'] for m in request['messages'] if '\nAssignment:\n' in str(m.get('content', '')))
                event, _ = json.JSONDecoder().raw_decode(prompt.split('\nAssignment:\n', 1)[1])
                tool_outputs = [m['content'] for m in request['messages'] if m['role'] == 'tool']
                finish = 'stop'
                if event.get('event') == 'plan':
                    if request.get('tools'):
                        raise AssertionError('Planning request advertised workspace tools')
                    if request.get('response_format') != {'type': 'json_object'}:
                        raise AssertionError('Planning must request native JSON output')
                    content = json.dumps(dict(contracts=children, reason='One bounded assignment.'))
                elif event.get('event') == 'review':
                    content = json.dumps(dict(decision='accept', reason='Actual independent QA passed.'))
                elif not tool_outputs:
                    files = event['contract']['OUTPUT']['files']
                    if event['phase'] == 'work':
                        parallel.wait(timeout=5)
                        code = f"from pathlib import Path; Path({files[0]!r}).write_text('result'); print('written')"
                    else:
                        code = f"from pathlib import Path; assert all(Path(p).read_text() == 'result' for p in {files!r}); print('verified actual output')"
                    message = dict(role='assistant', content=None, tool_calls=[dict(
                        id=f'call_{len(calls)}', type='function', function=dict(name='workspace_python', arguments=json.dumps({'code': code})))])
                    finish = 'tool_calls'
                elif event['phase'] == 'work':
                    content = json.dumps(dict(output={'files': event['contract']['OUTPUT']['files'], 'data': {}}))
                else:
                    evidence = re.findall(r'\bE\d+\b', str(tool_outputs[-1]))[0]
                    content = json.dumps(dict(checks=[dict(criterion=1, status='pass', method='execution',
                                                          evidence=[evidence], details='Executed an assertion on actual output.')], summary='Verified.'))
                if finish == 'stop':
                    message = dict(role='assistant', content=content)
                response = json.dumps(dict(id='test', object='chat.completion', created=0, model='fixture',
                                           choices=[dict(index=0, message=message, finish_reason=finish)],
                                           usage=dict(prompt_tokens=1, completion_tokens=1, total_tokens=2))).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(response)))
                self.end_headers()
                self.wfile.write(response)

        with tempfile.TemporaryDirectory() as directory, ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            root = Contract.parse(definition('project', 'Supervisor', 'QA One', files=('a.txt', 'b.txt')))
            config = [dict(name=name, role=str(folder / 'roles' / role), server=f'http://127.0.0.1:{server.server_port}/v1', model='fixture')
                      for name, role in [('Supervisor', 'Supervisor.md'), ('Coder A', 'Coder.md'), ('Coder B', 'Coder.md'), ('QA One', 'Tester.md'), ('QA Two', 'Tester.md')]]
            runtime = Runtime(root, directory, config)
            terminal = io.StringIO()
            try:
                with patch('mad_crewai.ui.console', Console(file=terminal, color_system=None)):
                    self.assertTrue(runtime.run())
            finally:
                server.shutdown()
                thread.join()
            evidence = list(runtime.workspace.evidence.values())
            qa = [item for item in evidence if item['phase'] == 'qa']
            self.assertEqual({item['contract'] for item in qa}, {'a', 'b', 'project'})
            self.assertTrue(all(item['returncode'] == 0 for item in qa))
            self.assertIn('verified actual output', qa[0]['stdout'])
            self.assertEqual((runtime.workspace.path / 'a.txt').read_text(), 'result')
            self.assertIn('[Tester]', terminal.getvalue())
            self.assertNotIn('write_text', terminal.getvalue())
            self.assertIn('workspace_python', (runtime.workspace.audit / 'crew-output.log').read_text())
