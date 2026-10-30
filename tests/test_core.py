import asyncio
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from rich.console import Console

from mad import ui

from mad.agent import Agent, parse_response, response_schema
from mad.messaging import Message, MessageHub
from mad.project import Project, ProjectState, Task
from mad.runtime import Runtime
from mad.workspace import Workspace


ROOT = Path(__file__).resolve().parent.parent


def team():
    return [Agent('Supervisor', '', 'Supervisor.md', 'fake'),
            Agent('Writer', '', 'Worker.md', 'fake')]


def definition(task_id='A', dependencies=None, owner='Writer', artifacts=None):
    return dict(id=task_id, title='Prepare the answer', description='Provide substantive output.',
                owner=owner, dependencies=dependencies or [], acceptance=['The conclusion is supported.'], artifacts=artifacts or [])


def evidence_id(call):
    for message in reversed(call['messages']):
        if message['role'] != 'user':
            continue
        content = message['content']
        data = json.loads(content if content.startswith('{') else content.split('\n\n', 1)[1])
        if 'id' in data:
            return data['id']
        if data.get('evidence'):
            return next(reversed(data['evidence']))
    raise AssertionError('No supplied evidence in this model context')


def accept(call):
    return dict(action='review', verdict='accept', reason='The actual output supports the conclusion.',
                evidence=[evidence_id(call)])


def finish(call):
    return dict(action='finish', accepted=True, reason='The combined deliverable meets the stated goal.',
                evidence=[evidence_id(call)])


class FakeModel:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    def chat(self, **call):
        self.calls.append(copy.deepcopy(call))
        response = next(self.responses, None)
        if response is None:
            raise RuntimeError('Unexpected model request in test')
        if isinstance(response, Exception):
            raise response
        if callable(response):
            response = response(call)
        return response if isinstance(response, str) else json.dumps(response)


def make_runtime(project, models):
    for agent in project.agents:
        agent._chat = models[agent.name].chat
    return Runtime(project)


class StateTests(unittest.TestCase):
    def setUp(self):
        self.project = Project('Goal', '.', agents=team())
        self.state = ProjectState(self.project)
        self.state.plan('Supervisor', [definition(), definition('B', ['A'])], initial=True)

    def test_only_supervisor_mutates_plan(self):
        with self.assertRaises(ValueError):
            self.state.plan('Writer', [definition('C')])
        self.assertEqual(len(self.state.tasks), 2)

    def test_worker_completion_requires_review_and_blocks_dependencies(self):
        self.state.start('A')
        self.state.submit('Writer', 'A', {'status': 'completed', 'output': 'Answer'}, ['E1'])
        self.assertEqual(self.state.get('A').status, 'review')
        self.assertFalse(self.state.ready())
        with self.assertRaises(ValueError):
            self.state.review('Writer', 'A', 'accept', 'Fine', ['E1'], {'E1': {}})
        self.state.review('Supervisor', 'A', 'accept', 'Actual answer checked', ['E1'], {'E1': {}})
        self.assertEqual([task.id for task in self.state.ready()], ['B'])

    def test_acceptance_needs_real_references(self):
        self.state.start('A')
        self.state.submit('Writer', 'A', {'status': 'completed'}, ['E1'])
        for refs in [[], ['invented']]:
            with self.subTest(refs=refs), self.assertRaises(ValueError):
                self.state.review('Supervisor', 'A', 'accept', 'Claim', refs, {'E1': {}})
        self.assertEqual(self.state.get('A').status, 'review')

    def test_wrong_owner_and_duplicate_results_are_rejected(self):
        self.state.start('A')
        with self.assertRaises(ValueError):
            self.state.submit('Supervisor', 'A', {'status': 'completed'}, [])
        self.state.submit('Writer', 'A', {'status': 'completed'}, [])
        with self.assertRaises(ValueError):
            self.state.submit('Writer', 'A', {'status': 'completed'}, [])

    def test_rework_keeps_correction_feedback(self):
        self.state.start('A')
        self.state.submit('Writer', 'A', {'status': 'completed'}, [])
        self.state.review('Supervisor', 'A', 'rework', 'Include the actual reasoning.', [], {})
        self.assertEqual(self.state.get('A').status, 'pending')
        self.assertIn('reasoning', self.state.get('A').feedback)

    def test_rework_limit_survives_replanning_and_preserves_feedback(self):
        for attempt in range(4):
            self.state.start('A')
            self.state.submit('Writer', 'A', {'status': 'blocked'}, [])
            self.state.review('Supervisor', 'A', 'rework', 'Add the missing source.', [], {})
            if attempt < 3:
                self.assertEqual(self.state.get('A').status, 'pending')
                self.assertIsNone(self.state.stop_reason)
                self.state.plan('Supervisor', [dict(definition(), description=f'Revised {attempt}')])
        self.assertEqual(self.state.attempts['A'], 4)
        self.assertEqual(self.state.get('A').status, 'blocked')
        self.assertIn('three rework retries', self.state.stop_reason)
        self.assertIn('Add the missing source.', self.state.get('A').feedback)
        with self.assertRaisesRegex(ValueError, 'exhausted'):
            self.state.plan('Supervisor', [definition()])
        self.assertFalse(self.state.ready())

    def test_replan_is_atomic_and_running_tasks_cannot_be_changed(self):
        self.state.start('A')
        before = [task.to_dict() for task in self.state.tasks]
        with self.assertRaises(ValueError):
            self.state.plan('Supervisor', [definition('C'), dict(definition(), title='Changed')])
        self.assertEqual([task.to_dict() for task in self.state.tasks], before)

    def test_cycles_missing_dependencies_and_fake_completed_tasks_are_rejected(self):
        before = [task.to_dict() for task in self.state.tasks]
        for changes in [[definition('C', ['missing'])], [definition('A', ['B'])],
                        [dict(definition('C'), status='completed')]]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.state.plan('Supervisor', changes)
            self.assertEqual([task.to_dict() for task in self.state.tasks], before)

    def test_replan_supports_reassignment_cancel_and_reopen(self):
        self.state.plan('Supervisor', [dict(definition('B', ['A']), owner='Supervisor')])
        self.assertEqual(self.state.get('B').owner, 'Supervisor')
        self.state.plan('Supervisor', [dict(definition('B'), status='cancelled')])
        self.assertEqual(self.state.get('B').status, 'cancelled')
        self.state.plan('Supervisor', [definition('B')])
        self.assertEqual(self.state.get('B').status, 'pending')

    def test_supervisor_role_is_identified_by_template_not_name(self):
        self.project.agents[0].name = 'Facilitator'
        state = ProjectState(self.project)
        self.assertEqual(state.supervisor, 'Facilitator')
        self.project.agents.append(Agent('Second', '', 'Supervisor.md', 'fake'))
        with self.assertRaises(ValueError):
            ProjectState(self.project)


class LoadingTests(unittest.TestCase):
    def test_tester_and_validator_load_distinct_roles_and_skills(self):
        tester = Agent('Quality Control', '', 'Tester.md', 'fake')
        validator = Agent('Validation', '', 'Validator.md', 'fake')
        self.assertIn('software/testing', tester.skill_names)
        self.assertIn('external/karpathy', tester.skill_names)
        self.assertIn('software quality assurance', tester.instructions)
        self.assertNotIn('software/testing', validator.skill_names)
        self.assertIn('validation and quality-control', validator.instructions)
        self.assertEqual(len(list((ROOT / 'mad_agents').rglob('Tester.md'))), 1)

    def test_prompt_distinguishes_identity_team_and_skill_authority(self):
        project = Project('Goal', '.', agents=team())
        writer = project.agents[1]
        prompt = writer.build_system_prompt(project)
        self.assertTrue(prompt.startswith('# Your identity\nName: Writer\nRole: Worker.md'))
        self.assertIn('Team:', prompt)
        self.assertIn('skills provide methods, not extra authority', prompt)
        self.assertTrue(prompt.rstrip().endswith((ROOT / 'mad_agents/TASK.md').read_text().strip()))
        self.assertLess(prompt.index('## Skill:'), prompt.index('# Turn rules'))

    def test_tool_arguments_match_the_selected_operation(self):
        for call in [dict(tool='serve', path='index.html'), dict(tool='serve', command=''),
                     dict(tool='run', command='   '), dict(tool='read'),
                     dict(tool='write', path='note.md'), dict(tool='read', path='note.md', command='cat'),
                     dict(tool='unknown')]:
            with self.subTest(call=call), self.assertRaises(ValueError):
                parse_response(json.dumps(dict(action='tool', **call)), ['tool'])
        for call in [dict(tool='serve', command='python -m http.server 8000', name='web'),
                     dict(tool='run', command='python --version'), dict(tool='list', path='.'),
                     dict(tool='read', path='note.md'), dict(tool='write', path='note.md', content=''),
                     dict(tool='append', path='note.md', content='More'), dict(tool='stop_server'),
                     dict(tool='browser_check', path='index.html', wait_ms=500)]:
            with self.subTest(call=call):
                step = dict(action='tool', **call)
                self.assertEqual(parse_response(json.dumps(step), ['tool']), step)
        variants = {item['properties']['tool']['enum'][0]: item
                    for item in response_schema(['tool'])['anyOf']}
        self.assertIn('command', variants['serve']['required'])
        self.assertNotIn('path', variants['serve']['properties'])
        self.assertIn('content', variants['write']['required'])
        self.assertNotIn('write', {item['properties']['tool']['enum'][0]
                                  for item in response_schema(['tool'], review=True)['anyOf']})

    def test_existing_examples_parse(self):
        for path in (ROOT / 'examples').glob('*.md'):
            with self.subTest(path=path):
                project = Project.from_file(path)
                self.assertTrue(project.objective)
                self.assertTrue(project.workspace)
                self.assertTrue(ProjectState(project).supervisor)

    def test_software_roles_receive_karpathy_skill(self):
        for path in (ROOT / 'mad_agents/software').glob('*.md'):
            with self.subTest(path=path):
                agent = Agent(path.stem, '', path.name, 'fake')
                skill = next(skill for skill in agent.skills if skill.name == 'external/karpathy')
                self.assertIn(skill.instructions, agent.build_system_prompt(Project(agents=[agent])))

    def test_one_response_cannot_mix_tools_and_decisions(self):
        with self.assertRaises(ValueError):
            parse_response(json.dumps(dict(action='tool', tool='list', path='.', verdict='accept')), ['tool', 'review'])

    def test_review_cannot_target_a_different_task(self):
        with self.assertRaises(ValueError):
            parse_response(json.dumps(dict(action='review', verdict='rework', reason='Fix T6', evidence=[], task_id='T6')), ['review'])

    def test_fenced_json(self):
        self.assertEqual(parse_response('```json\n{"action":"done"}\n```', ['done']), {'action': 'done'})


class WorkspaceTests(unittest.TestCase):
    def test_relative_aliases_audit_and_symlink_escapes(self):
        with tempfile.TemporaryDirectory() as parent:
            workspace = Workspace(Path(parent) / 'work')
            for path in ['./work', '././work/file.js', '../outside', '/tmp', '.mad/audit/events.jsonl']:
                with self.subTest(path=path), self.assertRaises(ValueError):
                    workspace.path(path)
            outside = Path(parent) / 'outside'
            outside.mkdir()
            (workspace.root / 'escape').symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                workspace.path('escape/file')
            self.assertNotIn('.mad', workspace.list_files())

    def test_read_before_write_and_failed_tools(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Workspace(root)
            workspace.write('note.md', 'original')
            reads = set()
            self.assertFalse(workspace.execute(dict(tool='write', path='note.md', content='bad'), reads)['success'])
            self.assertTrue(workspace.execute(dict(tool='read', path='./note.md'), reads)['success'])
            self.assertTrue(workspace.execute(dict(tool='write', path='note.md', content='new'), reads)['success'])
            self.assertFalse(workspace.execute(dict(tool='write', path='other.md', content='bad'), reads, review=True)['success'])
            self.assertEqual(workspace.read('note.md'), 'new')

    def test_command_output_cannot_forge_success(self):
        with tempfile.TemporaryDirectory() as root:
            result = Workspace(root).run(f'{sys.executable} -c "print(\'Return code: 0\'); raise SystemExit(7)"')
            self.assertFalse(result['success'])
            self.assertIn('Return code: 7', result['result'])

    def test_task_report_summarizes_evidence_without_dumping_file_bodies(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Workspace(root)
            task = Task('A', 'Prepare report', description='Explain the findings.', owner='Writer',
                        acceptance=['Supported conclusion'], artifacts=['note.md'], status='review',
                        result={'summary': 'Report ready', 'output': 'Findings supplied.'})
            workspace.record('Writer', dict(action='tool', tool='write', path='note.md', content='PRIVATE BODY'),
                             {'success': True, 'result': 'Wrote file'}, 'A')
            workspace.record('Writer', dict(action='tool', tool='run', command='check note.md'),
                             {'success': False, 'result': 'Missing source citation'}, 'A')
            workspace.record('Writer', dict(action='tool', tool='read', path='other.md'),
                             {'success': True, 'result': 'OTHER TASK'}, 'B')
            events = (workspace.audit / 'events.jsonl').read_text()
            path = workspace.task_report(task)
            report = path.read_text()
            for text in ['Explain the findings.', 'Supported conclusion', 'note.md',
                         'E1 — write note.md: OK', 'E2 — run check note.md: FAILED',
                         'Missing source citation', 'Report ready', 'Findings supplied.',
                         'Awaiting Supervisor review.', '[events.jsonl](events.jsonl)']:
                self.assertIn(text, report)
            self.assertNotIn('PRIVATE BODY', report)
            self.assertNotIn('OTHER TASK', report)
            task.status, task.feedback = 'pending', 'Rework: add a source citation.'
            workspace.task_report(task)
            self.assertIn('Rework: add a source citation.', path.read_text())
            self.assertNotIn('Awaiting Supervisor review.', path.read_text())
            self.assertEqual(events, (workspace.audit / 'events.jsonl').read_text())

    def test_audit_is_passive_and_preserves_failed_request(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Workspace(root)
            action = dict(action='tool', tool='read', path='absent.md')
            outcome = workspace.execute({'tool': 'read', 'path': 'absent.md'}, set())
            record = workspace.record('Writer', action, outcome, 'A')
            saved = json.loads((workspace.audit / 'events.jsonl').read_text())
            self.assertEqual(saved, record)
            self.assertFalse(saved['success'])
            self.assertEqual(saved['request']['path'], 'absent.md')
            self.assertNotIn('.mad', workspace.list_files())


class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def run_project(self, root, supervisor, writer, objective='Explain the tradeoff'):
        project = Project(objective, root, success=[objective], agents=team())
        runtime = make_runtime(project, {'Supervisor': supervisor, 'Writer': writer})
        with contextlib.redirect_stdout(io.StringIO()):
            result = await asyncio.wait_for(runtime.run(), 5)
        return runtime, result

    async def test_hub_delivery_and_unknown_recipients(self):
        hub = MessageHub()
        hub.register('Writer')
        message = Message('Supervisor', 'Writer', 'TASK')
        await hub.send(message)
        self.assertIs(await hub.receive('Writer'), message)
        with self.assertRaises(ValueError):
            await hub.send(Message('Writer', 'Missing', 'PEER'))

    async def test_inline_writing_full_lifecycle_without_software_tools(self):
        with tempfile.TemporaryDirectory() as root:
            supervisor = FakeModel(dict(action='plan', tasks=[definition()]), accept, finish)
            writer = FakeModel(dict(action='result', status='completed', summary='Compared options',
                                    output='A is faster but costs more. The supplied facts omit other risks.'))
            runtime, result = await self.run_project(root, supervisor, writer)
            self.assertTrue(result)
            self.assertEqual(runtime.state.get('A').status, 'completed')
            self.assertTrue(all(worker.done() for worker in runtime.workers))
            self.assertTrue((Path(root) / '.mad/audit/project-review.json').exists())
            self.assertFalse(any('tool' == record['request']['action'] for record in runtime.workspace.evidence.values()))

    async def test_file_writing_and_fresh_final_inspection(self):
        with tempfile.TemporaryDirectory() as root:
            supervisor = FakeModel(dict(action='plan', tasks=[definition(artifacts=['report.md'])]),
                                   dict(action='tool', tool='read', path='report.md'), accept,
                                   dict(action='tool', tool='read', path='report.md'), finish)
            writer = FakeModel(dict(action='tool', tool='write', path='report.md', content='Actual analysis, including uncertainty.'),
                               dict(action='result', status='completed', summary='Wrote analysis', output='report.md'))
            runtime, result = await self.run_project(root, supervisor, writer)
            self.assertTrue(result)
            self.assertEqual(Path(root, 'report.md').read_text(), 'Actual analysis, including uncertainty.')
            self.assertTrue(any(record['request'].get('tool') == 'read' for record in runtime.workspace.evidence.values()))

    async def test_rework_feedback_reaches_same_worker(self):
        with tempfile.TemporaryDirectory() as root:
            supervisor = FakeModel(dict(action='plan', tasks=[definition()]),
                                   dict(action='review', verdict='rework', reason='Include actual reasoning.', evidence=[]),
                                   accept, finish)
            writer = FakeModel(dict(action='result', status='completed', summary='Done', output='Just a claim'),
                               dict(action='result', status='completed', summary='Revised', output='The actual reasoning is provided.'))
            runtime, result = await self.run_project(root, supervisor, writer)
            self.assertTrue(result)
            second_context = json.loads(writer.calls[1]['messages'][1]['content'])
            self.assertEqual(second_context['task']['feedback'], 'Include actual reasoning.')
            self.assertEqual(len([m for m in runtime.hub.history() if m.type == 'TASK']), 2)

    async def test_new_result_clears_old_blocker_from_review_context(self):
        with tempfile.TemporaryDirectory() as root:
            blocker = 'Model response hit its token limit.'
            supervisor = FakeModel(dict(action='plan', tasks=[definition(artifacts=['result.md'])]),
                                   dict(action='review', verdict='rework', reason=blocker, evidence=[]),
                                   dict(action='tool', tool='read', path='result.md'), accept,
                                   dict(action='tool', tool='read', path='result.md'), finish)
            writer = FakeModel(dict(action='result', status='blocked', summary=blocker, output=''),
                               dict(action='tool', tool='write', path='result.md', content='Recovered substantive output.'),
                               dict(action='result', status='completed', summary='Created the result', output='result.md'))
            runtime, result = await self.run_project(root, supervisor, writer)
            self.assertTrue(result)
            retry_context = json.loads(writer.calls[1]['messages'][1]['content'])
            self.assertEqual(retry_context['task']['feedback'], blocker)
            review_context = json.loads(supervisor.calls[2]['messages'][1]['content'].split('\n\n', 1)[1])
            self.assertEqual(review_context['task']['status'], 'review')
            self.assertEqual(review_context['task']['result']['status'], 'completed')
            self.assertEqual(review_context['task']['feedback'], '')
            self.assertEqual(review_context['tasks'][0]['feedback'], '')
            self.assertNotIn(blocker, json.dumps(review_context))
            history = [json.loads(line) for line in (Path(root) / '.mad/audit/events.jsonl').read_text().splitlines()]
            self.assertTrue(any(record['request'].get('reason') == blocker for record in history))

    async def test_dependencies_unlock_only_after_acceptance(self):
        with tempfile.TemporaryDirectory() as root:
            supervisor = FakeModel(dict(action='plan', tasks=[definition(), definition('B', ['A'])]), accept, accept, finish)
            writer = FakeModel(dict(action='result', status='completed', summary='First', output='First finding'),
                               dict(action='result', status='completed', summary='Second', output='Synthesis of first finding'))
            runtime, result = await self.run_project(root, supervisor, writer)
            self.assertTrue(result)
            context = json.loads(writer.calls[1]['messages'][1]['content'])
            self.assertEqual(context['tasks'][0]['status'], 'completed')

    async def test_blocked_worker_is_reviewed_before_run_ends(self):
        with tempfile.TemporaryDirectory() as root:
            supervisor = FakeModel(dict(action='plan', tasks=[definition()]),
                                   dict(action='review', verdict='blocked', reason='Required information is unavailable.', evidence=[]))
            writer = FakeModel(dict(action='result', status='blocked', summary='Missing source', output='No source was supplied.'))
            runtime, result = await self.run_project(root, supervisor, writer)
            self.assertFalse(result)
            self.assertFalse(runtime.pending_reviews)
            self.assertEqual(runtime.state.get('A').feedback, 'Required information is unavailable.')

    async def test_worker_cannot_accept_own_task(self):
        with tempfile.TemporaryDirectory() as root:
            supervisor = FakeModel(dict(action='plan', tasks=[definition()]),
                                   dict(action='review', verdict='blocked', reason='Worker could not supply a valid result.', evidence=[]))
            invalid = dict(action='review', verdict='accept', reason='I approve myself', evidence=[])
            writer = FakeModel(invalid, invalid, invalid)
            runtime, result = await self.run_project(root, supervisor, writer)
            self.assertFalse(result)
            self.assertEqual(runtime.state.get('A').status, 'blocked')

    async def test_malformed_json_is_retried_within_current_turn(self):
        with tempfile.TemporaryDirectory() as root:
            supervisor = FakeModel(dict(action='plan', tasks=[definition()]), accept, finish)
            writer = FakeModel('invalid JSON', dict(action='result', status='completed', summary='Done', output='Actual text'))
            runtime, result = await self.run_project(root, supervisor, writer)
            self.assertTrue(result)
            self.assertEqual(len(writer.calls), 2)
            self.assertEqual(len([m for m in runtime.hub.history() if m.type == 'TASK']), 1)

    async def test_wrong_review_target_does_not_rerun_worker(self):
        with tempfile.TemporaryDirectory() as root:
            wrong = dict(action='review', task_id='T6', verdict='rework', reason='Wrong task', evidence=[])
            supervisor = FakeModel(dict(action='plan', tasks=[definition()]), wrong, wrong, wrong)
            writer = FakeModel(dict(action='result', status='completed', summary='Done', output='Actual text'))
            project = Project('Goal', root, agents=team())
            runtime = make_runtime(project, {'Supervisor': supervisor, 'Writer': writer})
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
                await asyncio.wait_for(runtime.run(), 5)
            self.assertEqual(runtime.state.get('A').status, 'review')
            self.assertFalse(runtime.project_accepted)
            self.assertEqual(len(writer.calls), 1)

    async def test_claimed_file_is_corrected_before_worker_submits(self):
        with tempfile.TemporaryDirectory() as root:
            supervisor = FakeModel(dict(action='plan', tasks=[definition(artifacts=['report.md'])]),
                                   accept, dict(action='tool', tool='read', path='report.md'), finish)
            writer = FakeModel(dict(action='result', status='completed', summary='File created', output='report.md: proposed contents'),
                               dict(action='tool', tool='write', path='report.md', content='Actual report with evidence.'),
                               dict(action='result', status='completed', summary='File now exists', output='report.md'))
            runtime, result = await self.run_project(root, supervisor, writer)
            self.assertTrue(result)
            error = json.loads(writer.calls[1]['messages'][-1]['content'])
            self.assertIn('Required artifact is missing: report.md', error['error'])
            self.assertEqual(Path(root, 'report.md').read_text(), 'Actual report with evidence.')
            self.assertEqual(len([m for m in runtime.hub.history() if m.type == 'TASK']), 1)
            self.assertEqual(len([m for m in runtime.hub.history() if m.type == 'REVIEW']), 1)

    async def test_removed_artifact_prevents_final_approval(self):
        with tempfile.TemporaryDirectory() as root:
            def remove_and_finish(call):
                Path(root, 'note.md').unlink()
                return finish(call)
            supervisor = FakeModel(dict(action='plan', tasks=[definition(artifacts=['note.md'])]),
                                   accept, remove_and_finish,
                                   dict(action='finish', accepted=False, reason='The required artifact is missing.', evidence=[]))
            writer = FakeModel(dict(action='tool', tool='write', path='note.md', content='Actual report.'),
                               dict(action='result', status='completed', summary='Created file', output='note.md'))
            runtime, result = await self.run_project(root, supervisor, writer)
            self.assertFalse(result)
            error = json.loads(supervisor.calls[3]['messages'][-1]['content'])
            self.assertIn('Required artifact is missing: note.md', error['error'])
            self.assertFalse(runtime.project_accepted)

    async def test_repeated_tool_failures_end_the_turn_without_looping(self):
        with tempfile.TemporaryDirectory() as root:
            supervisor = FakeModel(dict(action='plan', tasks=[definition()]),
                                   dict(action='review', verdict='blocked', reason='The task cannot proceed.', evidence=[]))
            failed = dict(action='tool', tool='read', path='missing.md')
            writer = FakeModel(failed, failed, failed)
            runtime, result = await self.run_project(root, supervisor, writer)
            self.assertFalse(result)
            self.assertEqual(len(writer.calls), 3)
            self.assertEqual(runtime.state.get('A').status, 'blocked')
            self.assertIn('repeated failed tools', runtime.state.get('A').result['summary'])

    async def test_repeated_successful_actions_are_not_applied_again(self):
        for step in [dict(action='message', recipient='Supervisor', content='Ready for review.'),
                     dict(action='tool', tool='write', path='note.md', content='Report')]:
            with self.subTest(action=step), tempfile.TemporaryDirectory() as root:
                project = Project('Goal', root, agents=team())
                agent = project.agents[1]
                model = FakeModel(step, step, step, step)
                agent._chat = model.chat
                applied = []

                async def apply(agent, action, event, reads, records):
                    applied.append(action)
                    return False, {'success': True}

                with self.assertRaisesRegex(RuntimeError, 'repeated invalid actions'):
                    await agent.work(project, Message('Supervisor', 'Writer', 'TASK', '{}'),
                                     ['tool', 'message', 'result'], apply)
                self.assertEqual(applied, [step])
                self.assertEqual(len(model.calls), 4)
                self.assertIn('already', json.loads(model.calls[2]['messages'][-1]['content'])['error'])

    async def test_duplicate_action_feedback_allows_finishing(self):
        with tempfile.TemporaryDirectory() as root:
            project = Project('Goal', root, agents=team())
            agent = project.agents[1]
            write = dict(action='tool', tool='write', path='note.md', content='Report')
            changed = dict(write, content='Revised report')
            result = dict(action='result', status='completed', summary='Ready', output='Report')
            model = FakeModel(write, dict(write, path='./note.md'), changed, result)
            agent._chat = model.chat
            applied = []

            async def apply(agent, action, event, reads, records):
                applied.append(action)
                return action['action'] == 'result', {'success': True}

            await agent.work(project, Message('Supervisor', 'Writer', 'TASK', '{}'),
                             ['tool', 'result'], apply)
            self.assertEqual(applied, [write, changed, result])

    async def test_unchanged_inspection_cycle_stops_and_can_recover(self):
        for recover in (False, True):
            with self.subTest(recover=recover), tempfile.TemporaryDirectory() as root:
                project = Project('Goal', root, agents=team())
                agent = project.agents[1]
                cycle = [dict(action='tool', tool='list', path='.'),
                         dict(action='tool', tool='read', path='script.js'),
                         dict(action='tool', tool='browser_check', path='index.html')]
                finish = dict(action='result', status='completed', summary='Checked', output='Findings')
                model = FakeModel(*(cycle * 2 + ([finish] if recover else cycle)))
                agent._chat = model.chat
                count = 0

                async def apply(agent, action, event, reads, records):
                    nonlocal count
                    count += 1
                    output = 'unchanged'
                    if action.get('tool') == 'browser_check':
                        output = json.dumps(dict(url=f'http://localhost:{count}/', status=200,
                                                 title='Page', errors=[], warnings=[str(count)]))
                    return action['action'] == 'result', {'id': f'E{count}', 'success': True, 'result': output}

                event = Message('Supervisor', 'Writer', 'TASK', '{}')
                if recover:
                    await agent.work(project, event, ['tool', 'result'], apply)
                    self.assertEqual(len(model.calls), 7)
                    self.assertIn('Repeated inspections', model.calls[-1]['messages'][-1]['content'])
                else:
                    with self.assertRaisesRegex(RuntimeError, 'Repeated inspections'):
                        await agent.work(project, event, ['tool', 'result'], apply)
                    self.assertEqual(len(model.calls), 8)

    async def test_inspections_allow_changed_results_and_rechecking_after_write(self):
        with tempfile.TemporaryDirectory() as root:
            project = Project('Goal', root, agents=team())
            agent = project.agents[1]
            read = dict(action='tool', tool='read', path='note.md')
            write = dict(action='tool', tool='write', path='note.md', content='Updated')
            finish = dict(action='result', status='completed', summary='Ready', output='Updated')
            model = FakeModel(*([read] * 5 + [write] + [read] * 3 + [finish]))
            agent._chat = model.chat
            count = 0

            async def apply(agent, action, event, reads, records):
                nonlocal count
                count += 1
                return action['action'] == 'result', {'success': True, 'result': str(count) if count < 6 else 'Updated'}

            await agent.work(project, Message('Supervisor', 'Writer', 'TASK', '{}'), ['tool', 'result'], apply)
            self.assertEqual(len(model.calls), 10)

    async def test_rework_limit_stops_project_unaccepted_and_audits_reason(self):
        with tempfile.TemporaryDirectory() as root:
            rework = dict(action='review', verdict='rework', reason='Add supporting evidence.', evidence=[])
            supervisor = FakeModel(dict(action='plan', tasks=[definition()]), *([rework] * 4))
            writer = FakeModel(*([dict(action='result', status='blocked', summary='Cannot verify', output='Findings')] * 4))
            runtime, accepted = await self.run_project(root, supervisor, writer)
            self.assertFalse(accepted)
            self.assertEqual(len(writer.calls), 4)
            self.assertEqual(len(supervisor.calls), 5)
            self.assertEqual(runtime.state.get('A').status, 'blocked')
            self.assertIn('exhausted its three rework retries', (Path(root) / '.mad/audit/A.md').read_text())
            events = [json.loads(line) for line in (Path(root) / '.mad/audit/events.jsonl').read_text().splitlines()]
            self.assertEqual(events[-1]['request']['action'], 'review')
            self.assertFalse(any(message.type == 'PROJECT' for message in runtime.hub.history()))

    def test_review_context_places_template_guidance_before_task_and_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            project = Project('Goal', root, agents=team())
            runtime = Runtime(project)
            runtime.state.plan('Supervisor', [definition(), definition('B', ['A'])], initial=True)
            task = runtime.state.get('A')
            records = {'E1': {'result': 'Evidence ' * 10000}}
            context = runtime._context('review', task, records)
            instruction = project.agents[0].review_instruction
            self.assertIn('acceptance criteria', instruction)
            self.assertIn(instruction, context)
            self.assertTrue(context.startswith(instruction + '\n\n'))
            self.assertNotIn('review_instruction', json.loads(runtime._context('task', task)))
            project.agents[0].review_instruction = 'Template-specific review guidance.'
            self.assertTrue(runtime._context('review', task).startswith('Template-specific review guidance.\n\n'))

    async def test_review_schema_does_not_offer_write_tools(self):
        with tempfile.TemporaryDirectory() as root:
            supervisor = FakeModel(dict(action='plan', tasks=[definition()]), accept, finish)
            writer = FakeModel(dict(action='result', status='completed', summary='Explained', output='A real conclusion.'))
            runtime, result = await self.run_project(root, supervisor, writer)
            self.assertTrue(result)
            def offered_tools(call):
                return [tool for variant in call['json_schema']['anyOf']
                        if variant['properties']['action']['enum'] == ['tool']
                        for tool in variant['properties']['tool']['enum']]
            self.assertIn('write', offered_tools(writer.calls[0]))
            self.assertNotIn('write', offered_tools(supervisor.calls[1]))
            self.assertNotIn('append', offered_tools(supervisor.calls[1]))

    async def test_supervisor_assigned_work_uses_the_same_agent_loop(self):
        with tempfile.TemporaryDirectory() as root:
            supervisor = FakeModel(dict(action='plan', tasks=[definition(owner='Supervisor')]),
                                   dict(action='result', status='completed', summary='Analyzed', output='Substantive analysis.'),
                                   accept, finish)
            project = Project('Analyze supplied facts', root, agents=team()[:1])
            runtime = make_runtime(project, {'Supervisor': supervisor})
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(await asyncio.wait_for(runtime.run(), 5))
            self.assertEqual(runtime.state.get('A').owner, 'Supervisor')
            self.assertEqual([m.type for m in runtime.hub.history()], ['PLAN', 'TASK', 'REVIEW', 'PROJECT'])

    async def test_peer_messages_use_the_same_inbox_loop(self):
        with tempfile.TemporaryDirectory() as root:
            supervisor = FakeModel(dict(action='plan', tasks=[definition()]), dict(action='done'), accept, finish)
            writer = FakeModel(dict(action='message', recipient='Supervisor', content='A useful finding.'),
                               dict(action='result', status='completed', summary='Finished', output='A substantiated conclusion.'))
            runtime, result = await self.run_project(root, supervisor, writer)
            self.assertTrue(result)
            peers = [m for m in runtime.hub.history() if m.type == 'PEER']
            self.assertEqual(len(peers), 1)
            peer_context = json.loads(supervisor.calls[1]['messages'][1]['content'])
            self.assertEqual(peer_context['sender'], 'Writer')
            self.assertEqual(peer_context['task_id'], 'A')
            self.assertEqual((peers[0].sender, peers[0].recipient, peers[0].content),
                             ('Writer', 'Supervisor', 'A useful finding.'))

    async def test_peer_question_gets_one_reply_without_a_reply_chain(self):
        with tempfile.TemporaryDirectory() as root:
            project = Project('Goal', root, agents=team())
            runtime = Runtime(project)
            for agent in project.agents:
                runtime.hub.register(agent.name)
            question = Message('Writer', 'Supervisor', 'PEER', 'Which source should I use?', 'A')
            await runtime.hub.send(question)
            supervisor, writer = project.agents
            reply_action = dict(action='message', recipient='Writer', content='Use the supplied source.')
            # A model that always emits messages must not get another send opportunity.
            supervisor_chat = FakeModel(*[reply_action for _ in range(101)])
            supervisor._chat = supervisor_chat.chat
            event = await runtime.hub.receive('Supervisor')
            await supervisor.work(project, event, runtime._actions(supervisor, event), runtime._apply)
            self.assertEqual(len(supervisor_chat.calls), 1)
            reply = await runtime.hub.receive('Writer')
            self.assertEqual((reply.sender, reply.recipient, reply.task_id), ('Supervisor', 'Writer', 'A'))
            self.assertTrue(reply.metadata['reply'])
            writer_chat = FakeModel(dict(action='done'))
            writer._chat = writer_chat.chat
            await writer.work(project, reply, runtime._actions(writer, reply), runtime._apply)
            self.assertEqual(len(writer_chat.calls), 1)
            context = json.loads(writer_chat.calls[0]['messages'][1]['content'])
            self.assertTrue(context['reply'])
            self.assertEqual(context['content'], 'Use the supplied source.')
            self.assertEqual(len(runtime.hub.history()), 2)
            self.assertTrue(all(inbox.empty() for inbox in runtime.hub.inboxes.values()))

    async def test_peer_reply_cannot_send_another_message(self):
        with tempfile.TemporaryDirectory() as root:
            project = Project('Goal', root, agents=team())
            runtime = Runtime(project)
            for agent in project.agents:
                runtime.hub.register(agent.name)
            reply = Message('Supervisor', 'Writer', 'PEER', 'Use the supplied source.', 'A', {'reply': True})
            self.assertEqual(runtime._actions(project.agents[1], reply), ['done'])
            with self.assertRaises(ValueError):
                await runtime._apply(project.agents[1],
                                     dict(action='message', recipient='Supervisor', content='Acknowledged.'),
                                     reply, set(), [])
            self.assertFalse(runtime.hub.history())

    async def test_independent_agents_act_concurrently(self):
        with tempfile.TemporaryDirectory() as root:
            barrier = threading.Barrier(2, timeout=2)

            def simultaneous_result(call):
                barrier.wait()
                return dict(action='result', status='completed', summary='Analyzed', output='An independent finding.')

            agents = team() + [Agent('Analyst', '', 'Expert.md', 'fake')]
            supervisor = FakeModel(dict(action='plan', tasks=[definition(), definition('B', owner='Analyst')]),
                                   accept, accept, finish)
            models = {'Supervisor': supervisor, 'Writer': FakeModel(simultaneous_result),
                      'Analyst': FakeModel(simultaneous_result)}
            runtime = make_runtime(Project('Compare findings', root, agents=agents), models)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(await asyncio.wait_for(runtime.run(), 5))
            self.assertTrue(all(task.status == 'completed' for task in runtime.state.tasks))

    async def test_final_replan_requires_another_final_review(self):
        with tempfile.TemporaryDirectory() as root:
            supervisor = FakeModel(dict(action='plan', tasks=[definition()]), accept,
                                   dict(action='replan', tasks=[definition('B')], reason='Add a missing synthesis.'),
                                   accept, finish)
            writer = FakeModel(dict(action='result', status='completed', summary='First', output='Initial findings.'),
                               dict(action='result', status='completed', summary='Second', output='Combined synthesis.'))
            runtime, result = await self.run_project(root, supervisor, writer)
            self.assertTrue(result)
            self.assertEqual(len([m for m in runtime.hub.history() if m.type == 'PROJECT']), 2)
            self.assertEqual(len(runtime.state.tasks), 2)

    async def test_unrelated_replan_does_not_resolve_current_review(self):
        with tempfile.TemporaryDirectory() as root:
            changed = dict(definition('B', ['A']), description='Add a missing source comparison.')
            supervisor = FakeModel(dict(action='plan', tasks=[definition(), definition('B', ['A'])]),
                                   dict(action='replan', tasks=[changed], reason='Clarify the next task.'),
                                   accept, accept, finish)
            writer = FakeModel(dict(action='result', status='completed', summary='First', output='Finding A.'),
                               dict(action='result', status='completed', summary='Second', output='Finding B.'))
            runtime, result = await self.run_project(root, supervisor, writer)
            self.assertTrue(result)
            feedback = json.loads(supervisor.calls[2]['messages'][-1]['content'])
            self.assertIn('Decide the current task A', feedback['remaining'])
            self.assertEqual(len([m for m in runtime.hub.history() if m.type == 'REVIEW']), 2)

    async def test_cancellation_during_final_review_never_approves_project(self):
        with tempfile.TemporaryDirectory() as root:
            supervisor = FakeModel(dict(action='plan', tasks=[definition()]), accept)
            writer = FakeModel(dict(action='result', status='completed', summary='Finished', output='Actual explanation.'))
            runtime = make_runtime(Project('Goal', root, agents=team()),
                                   {'Supervisor': supervisor, 'Writer': writer})
            started = asyncio.Event()
            work = Agent.work

            async def wait_at_final(agent, project, event, actions, apply):
                if event.type == 'PROJECT':
                    started.set()
                    await asyncio.Future()
                else:
                    await work(agent, project, event, actions, apply)

            with patch.object(Agent, 'work', wait_at_final), contextlib.redirect_stdout(io.StringIO()):
                running = asyncio.create_task(runtime.run())
                try:
                    await asyncio.wait_for(started.wait(), 5)
                finally:
                    running.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await running
            self.assertFalse(runtime.project_accepted)
            self.assertTrue(all(worker.done() for worker in runtime.workers))

    async def test_final_approval_rejects_only_old_file_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            # Final review first tries old submission evidence, then inspects the real file.
            def old_finish(call):
                return dict(action='finish', accepted=True, reason='Old claim', evidence=[
                    ref for ref, record in json.loads(call['messages'][1]['content'])['evidence'].items()
                    if record['request']['action'] == 'result'][0:1])
            supervisor = FakeModel(dict(action='plan', tasks=[definition()]), accept, old_finish,
                                   dict(action='tool', tool='read', path='note.md'), finish)
            writer = FakeModel(dict(action='tool', tool='write', path='note.md', content='A substantive note.'),
                               dict(action='result', status='completed', summary='Wrote note', output='note.md'))
            runtime, result = await self.run_project(root, supervisor, writer)
            self.assertTrue(result)
            self.assertIn('fresh evidence', supervisor.calls[3]['messages'][-1]['content'])


class AdapterTests(unittest.TestCase):
    @patch('mad.agent.requests.post')
    def test_truncated_response_is_reported_explicitly(self, post):
        post.return_value = Mock(ok=True)
        post.return_value.json.return_value = {'choices': [{'finish_reason': 'length', 'message': {'content': '{'}}]}
        with self.assertRaisesRegex(ValueError, 'token limit'):
            team()[1]._chat([])

    @patch('mad.agent.requests.post')
    def test_http_endpoint_and_structured_output(self, post):
        post.return_value = Mock(ok=True)
        post.return_value.json.return_value = {'choices': [{'finish_reason': 'stop', 'message': {'content': '<think>hidden</think> {}'}}]}
        self.assertEqual(Agent('Writer', '', 'Worker.md', 'fake', server='http://server/v1/')._chat([], json_schema={'type': 'object'}), '{}')
        args, kwargs = post.call_args
        self.assertEqual(args[0], 'http://server/v1/chat/completions')
        self.assertEqual(kwargs['json']['response_format']['type'], 'json_schema')


class UITests(unittest.TestCase):
    def test_colored_output_preserves_literal_project_and_model_text(self):
        output = io.StringIO()
        project = Project('[bold]Literal objective[/bold]', '.', ['A checked result'], team())
        task = Task('A', 'Write [example]', owner='Writer', status='review',
                    result={'summary': '[bold]Literal summary[/bold]', 'output': '[unknown]Literal output[/unknown]'})
        with patch.object(ui, 'console', Console(file=output, width=200, force_terminal=True, color_system='standard')):
            for verbose in [False, True]:
                ui.start(project, Path('/tmp/workspace'), verbose)
            ui.plan([task])
            ui.activity(project.agents[1], Message('Supervisor', 'Writer', 'TASK', task_id='A'))
            ui.tool_result('Writer', {'tool': 'read', 'path': 'note.md'}, {'success': False})
            ui.task_result(task, Path('/tmp/workspace/.mad/audit/A.md'))
            ui.review(task, 'accept', '[bold]Literal reason[/bold]')
            ui.finish(True, 'Verified')
            ui.finish(False, 'Needs correction')
            ui.error('[unknown]Literal error[/unknown]')
        rendered = output.getvalue()
        self.assertIn('\x1b[', rendered)
        for text in ['MULTI AGENT DRIVER', 'Initial Task Graph', 'Supervisor Review',
                     'Project accepted', 'Project not accepted', 'Skills',
                     project.objective, task.result['summary'], task.result['output'],
                     '[bold]Literal reason[/bold]', '[unknown]Literal error[/unknown]',
                     '/tmp/workspace/.mad/audit/A.md']:
            self.assertIn(text, rendered)
