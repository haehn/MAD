import io
import json
from pathlib import Path
import tempfile
from threading import Barrier, Event, Lock
import unittest
from unittest.mock import patch

from crewai.llms.base_llm import BaseLLM
from rich.console import Console

from mad.contracts import Acceptance, Completion, Contract, Delivery, Plan, QCResult
from mad.runtime import Runtime, workspace_path
from mad import ui


def contract(name='write', role='Writer', output=None, dependencies=None):
    return Contract(id=name, role=role, input='Use the supplied facts.', task='Write the requested result.',
                    output=output or [], acceptance=['The delivered result is correct.'], dependencies=dependencies or [])


class ScriptedLLM(BaseLLM):
    """Controlled model responses; the real CrewAI Task/Crew executes all plumbing."""
    def __init__(self, script):
        super().__init__(model='fixture', temperature=0)
        self.script = script

    def supports_function_calling(self):
        return True

    def call(self, messages, tools=None, callbacks=None, available_functions=None, **kwargs):
        return self.script(kwargs['from_task'])


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.runtime = Runtime('Write the project result.', {'Supervisor': 'Coordinate.', 'Writer': 'Write.',
                               'Other Writer': 'Write.', 'QC': '# Reviewer\nCheck actual results.'},
                               self.directory.name, 'http://fixture/v1', 'fixture')
        self.plan = [contract()]
        self.calls, self.states = [], []
        self.guard = Lock()
        self.callback = self.respond
        self.agent_done = Event()
        case = self

        def record(event, **data):
            if event == 'contract_state':
                with case.guard:
                    case.states.append((data['contract']['id'], data['contract']['state']))
            original_record(event, **data)

        original_record = self.runtime.record
        self.runtime.record = record
        self.terminal = io.StringIO()
        self.addCleanup(patch.stopall)
        patch.object(ui, 'console', Console(file=self.terminal, force_terminal=False)).start()
        patch('crewai.LLM', side_effect=lambda **kwargs: ScriptedLLM(self.invoke)).start()

    def invoke(self, task):
        with self.guard:
            self.calls.append(task.name)
        return self.callback(task)

    def respond(self, task):
        if task.name == 'plan':
            return Plan(contracts=self.plan)
        if task.name.startswith('work-'):
            item = self.runtime.contracts[task.name.removeprefix('work-')]
            for name in item.output:
                code = f'from pathlib import Path; Path({name!r}).write_text("actual output"); print("written")'
                task.tools[0].run(code=code)
            return Delivery(output='The actual substantive result.', files=list(item.output))
        if task.name.startswith('qc-'):
            return QCResult(status='PASS', findings='Inspected the actual delivery against the criterion.')
        if task.name.startswith('accept-'):
            return Acceptance(accepted=[item.id for item in self.runtime.contracts.values() if item.state == 'QC'])
        if task.name == 'project-review':
            self.assertTrue(all(c.state == 'ACCEPTED' for c in self.runtime.contracts.values()))
            return Completion(complete=True, summary='The accepted project satisfies the goal.')
        raise AssertionError(task.name)

    def run_project(self):
        return self.runtime.run()

    def test_complete_native_lifecycle_and_only_supervisor_finishes(self):
        self.assertIn('accepted project', self.run_project())
        self.assertEqual(self.states, [('write', 'OPEN'), ('write', 'DELIVERED'), ('write', 'QC'), ('write', 'ACCEPTED')])
        self.assertEqual(self.calls, ['plan', 'work-write', 'qc-write', 'accept-write', 'project-review'])
        self.assertIn('MULTI AGENT DRIVER', self.terminal.getvalue())
        self.assertIn('◆ QC · write · PASS', self.terminal.getvalue())
        self.assertIn('◆ Supervisor · write · ACCEPTED', self.terminal.getvalue())

    def test_qc_failure_uses_native_guardrail_retry_before_acceptance(self):
        count = 0
        original = self.respond

        def script(task):
            nonlocal count
            if task.name == 'qc-write':
                count += 1
                return QCResult(status='FAIL' if count == 1 else 'PASS', findings='Correct the missing comparison.' if count == 1 else 'Correction verified.')
            return original(task)

        self.callback = script
        self.run_project()
        self.assertEqual(self.calls.count('work-write'), 2)
        self.assertEqual(self.calls.count('qc-write'), 2)
        self.assertIn(('write', 'OPEN'), self.states[3:])
        self.assertEqual(self.states[-1], ('write', 'ACCEPTED'))

    def test_qc_exhaustion_never_accepts_or_finishes(self):
        original = self.respond
        self.callback = lambda task: QCResult(status='FAIL', findings='Required behavior failed.') if task.name.startswith('qc-') else original(task)
        with self.assertRaises(Exception):
            self.run_project()
        self.assertEqual(self.runtime.contracts['write'].state, 'OPEN')
        self.assertNotIn('project-review', self.calls)
        self.assertNotIn('accept-write', self.calls)
        self.assertEqual(self.calls.count('qc-write'), 3)

    def test_independent_work_overlaps_and_each_delivery_goes_straight_to_qc(self):
        self.plan = [contract('a', 'Writer'), contract('b', 'Other Writer')]
        started = Barrier(2)
        qa_started = Event()
        original = self.respond

        def script(task):
            if task.name in ('work-a', 'work-b'):
                started.wait(timeout=3)
                if task.name == 'work-b':
                    self.assertTrue(qa_started.wait(timeout=3), 'QC for a should run before b finishes.')
            if task.name == 'qc-a':
                qa_started.set()
            return original(task)

        self.callback = script
        self.run_project()
        self.assertTrue(qa_started.is_set())
        self.assertEqual([c.state for c in self.runtime.contracts.values()], ['ACCEPTED', 'ACCEPTED'])

    def test_dependent_work_starts_only_after_supervisor_acceptance(self):
        self.plan = [contract('design', output=['design.txt']), contract('implementation', 'Other Writer', ['result.txt'], ['design'])]
        original = self.respond

        def script(task):
            if task.name == 'work-implementation':
                self.assertEqual(self.runtime.contracts['design'].state, 'ACCEPTED')
                self.assertTrue(Path(self.directory.name, 'design.txt').exists())
                self.assertEqual(task.context[0].name, 'work-design')
                self.assertIn('actual output', task.context[0].output.pydantic.files and Path(self.directory.name, 'design.txt').read_text())
            return original(task)

        self.callback = script
        self.run_project()
        self.assertLess(self.calls.index('accept-design'), self.calls.index('work-implementation'))

    def test_inline_delivery_cannot_be_replaced_by_an_undeclared_file(self):
        original = self.respond

        def script(task):
            if task.name == 'work-write':
                Path(self.directory.name, 'unexpected.txt').write_text('substitute file')
                return Delivery(output='See the file.', files=['unexpected.txt'])
            return original(task)

        self.callback = script
        with self.assertRaises(Exception):
            self.run_project()
        self.assertEqual(self.calls.count('qc-write'), 3)
        self.assertNotIn('project-review', self.calls)

    def test_same_configured_worker_is_not_executed_in_parallel_with_itself(self):
        self.plan = [contract('a'), contract('b')]
        self.run_project()
        self.assertLess(self.calls.index('accept-a'), self.calls.index('work-b'))

    def test_missing_file_cannot_pass_even_if_qc_claims_pass(self):
        self.plan = [contract(output=['missing.txt'])]
        original = self.respond

        def script(task):
            if task.name == 'work-write':
                return Delivery(output='Claimed creation.', files=['missing.txt'])
            return original(task)

        self.callback = script
        with self.assertRaises(Exception):
            self.run_project()
        self.assertGreater(self.calls.count('qc-write'), 0)
        self.assertNotIn('project-review', self.calls)
        self.assertNotEqual(self.runtime.contracts['write'].state, 'ACCEPTED')

    def test_supervisor_declining_project_is_failure(self):
        original = self.respond
        self.callback = lambda task: Completion(complete=False, summary='Combined requirements are incomplete.') if task.name == 'project-review' else original(task)
        with self.assertRaisesRegex(RuntimeError, 'Supervisor did not accept'):
            self.run_project()
        self.assertNotIn('Project finished', self.terminal.getvalue())

    def test_overlapping_outputs_need_dependencies(self):
        self.plan = [contract('a', output=['same.txt']), contract('b', 'Other Writer', ['same.txt'])]
        ok, error = self.runtime.validate_plan(type('Output', (), {'pydantic': Plan(contracts=self.plan)})())
        self.assertFalse(ok)
        self.assertIn('overlapping writers', error)
        self.plan[1].dependencies = ['a']
        self.assertTrue(self.runtime.validate_plan(type('Output', (), {'pydantic': Plan(contracts=self.plan)})())[0])

    def test_unknown_dependencies_and_roles_are_rejected(self):
        for item in (contract(role='Missing'), contract(dependencies=['future'])):
            with self.subTest(item=item):
                ok, _ = self.runtime.validate_plan(type('Output', (), {'pydantic': Plan(contracts=[item])})())
                self.assertFalse(ok)

    def test_workspace_paths_cannot_escape_or_reference_audits(self):
        for name in ('../outside', '/tmp/outside', '.mad/audit/log.md'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                workspace_path(self.directory.name, name)

    def test_audit_keeps_actual_execution_and_delivery_details(self):
        self.plan = [contract(output=['result.txt'])]
        self.run_project()
        events = [json.loads(line) for line in self.runtime.log.read_text().splitlines()]
        actual = next(e for e in events if e['event'] == 'python_finished')
        self.assertEqual(actual['returncode'], 0)
        self.assertEqual(actual['stdout'], 'written\n')
        self.assertTrue(any(e['event'] == 'quality_control' for e in events))
        self.assertTrue(any(e['event'] == 'model_response' for e in events))
        self.assertIn('The actual substantive result.', self.runtime.log.with_suffix('.md').read_text())
        self.assertTrue(self.runtime.log.with_suffix('.crew.log').exists())

    def test_existing_examples_complete_with_native_crewai_and_controlled_deliveries(self):
        from mad.runtime import read_project
        root = Path(__file__).resolve().parent.parent
        for path in sorted((root / 'examples').glob('*.md')):
            with self.subTest(example=path.name), tempfile.TemporaryDirectory() as directory:
                objective, roles, _, models = read_project(path)
                runtime = Runtime(objective, roles, directory, 'http://fixture/v1', 'fixture', models)
                worker = next(name for name in roles if name != runtime.supervisor and name != runtime.qc)

                def script(task):
                    if task.name == 'plan':
                        return Plan(contracts=[contract(role=worker)])
                    if task.name.startswith('work-'):
                        return Delivery(output='Controlled example delivery.', files=[])
                    if task.name.startswith('qc-'):
                        return QCResult(status='PASS', findings='Controlled acceptance check.')
                    if task.name.startswith('accept-'):
                        return Acceptance(accepted=['write'])
                    return Completion(complete=True, summary='Controlled example lifecycle completed.')

                with patch('crewai.LLM', side_effect=lambda **kwargs: ScriptedLLM(script)):
                    runtime.run()
                self.assertEqual(runtime.contracts['write'].state, 'ACCEPTED')
