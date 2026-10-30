"""Review contracts apply to artifacts and substantive non-software outputs."""
import asyncio
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from mad.message import Message
from mad.supervisor_protocol import SupervisorProtocol
from mad.project import Project
from mad.project_state import ProjectState
from mad.runtime import Runtime
from mad.tool_session import ToolSession
from mad.workspace import Workspace
from test_architecture import ScriptedModel, accept, assessment, reporter, task_data, team


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.project = Project('Goal', '.', agents=team())
        self.state = ProjectState(self.project)
        self.state.install_plan({'tasks': [task_data()]})
        self.state.start(self.project.tasks[0])
        self.state.record_result('T1', 'Worker', 'completed')
        self.state.evidence['T1'] = {'submission': {'output': 'Actual output'}}

    def test_unsupported_acceptance_is_rejected(self):
        actions = [
            {'type': 'accept', 'task_id': 'T1'},
            {'type': 'accept', 'task': {'id': 'T1'}, 'assessments': [assessment()]},
            dict(accept(), assessments=[]),
            dict(accept(), assessments=[assessment('Different criterion')]),
            dict(accept(), assessments=[assessment(evidence='invented-check')]),
            dict(accept(), assessments=[dict(assessment(), reason='')]),
        ]
        for action in actions:
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.state.decide([action], 'T1')
            self.assertEqual(self.project.tasks[0].status, 'review')

    def test_each_criterion_needs_its_own_assessment(self):
        self.project.tasks[0].acceptance.append('Evidence is explained')
        with self.assertRaises(ValueError):
            self.state.decide([accept()], 'T1')
        action = accept()
        action['assessments'].append(assessment('Evidence is explained'))
        self.state.decide([action], 'T1')
        self.assertEqual(self.project.tasks[0].status, 'completed')

    def test_no_explicit_criteria_uses_task_description(self):
        self.project.tasks[0].acceptance = []
        self.project.tasks[0].description = 'Explain the tradeoff'
        self.state.decide([accept(criterion='Explain the tradeoff')], 'T1')
        self.assertEqual(self.project.tasks[0].status, 'completed')


class ReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_records_include_requests_contents_and_failures(self):
        with tempfile.TemporaryDirectory() as parent:
            workspace = Workspace(Path(parent) / 'work')
            tools = ToolSession(workspace, reporter())
            results = await tools.execute([
                {'tool': 'list', 'path': './work'},
                {'tool': 'write', 'path': 'notes.md', 'content': 'Evidence and limitations'},
                {'tool': 'read', 'path': 'notes.md'},
            ])
            self.assertFalse(tools.records[0]['success'])
            self.assertEqual(tools.records[0]['request']['path'], './work')
            self.assertEqual(tools.records[0]['normalized_request']['path'], 'work')
            self.assertEqual(tools.records[1]['request']['content'], 'Evidence and limitations')
            self.assertEqual(tools.records[2]['result'], 'Evidence and limitations')
            self.assertEqual(results[2]['evidence_id'], 'tool_3')

    async def test_review_cannot_directly_write_deliverables(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Workspace(root)
            tools = ToolSession(workspace, reporter(), allowed_tools={'read', 'list'})
            result = await tools.execute([{'tool': 'write', 'path': 'report.md', 'content': 'changed'}])
            self.assertFalse(result[0]['success'])
            self.assertFalse(workspace.exists('report.md'))

    async def test_inline_research_can_be_accepted_without_software_tools(self):
        with tempfile.TemporaryDirectory() as root:
            criterion = 'Explain conflicting findings and uncertainty'
            output = ('In this fictional dataset, source A reports a gain while source B '
                      'reports no change. Their sampling differs, so a causal conclusion '
                      'requires comparable samples. The evidence remains uncertain.')
            project = Project(criterion, root, agents=team()[:2])
            task = dict(task_data(), acceptance=[criterion])
            supervisor = ScriptedModel([
                {'tasks': [task]},
                {'reason': 'Assessed the supplied analysis', 'actions': [accept(criterion=criterion)]},
                {'accepted': True, 'reason': 'Combined answer directly addresses the objective',
                 'actions': [], 'assessments': [assessment(criterion, 'T1:submission')]},
            ])
            worker = ScriptedModel([
                {'state': 'final'},
                {'status': 'completed', 'summary': 'Compared evidence', 'output': output, 'messages': []},
            ])
            runtime = Runtime(project, model_factory=lambda agent: supervisor if agent.name == 'Supervisor' else worker,
                              reporter=reporter())
            await asyncio.wait_for(runtime.run(), 5)
            self.assertTrue(runtime.project_accepted)
            evidence = runtime.state.evidence['T1']
            self.assertEqual(evidence['submission']['output'], output)
            self.assertEqual(set(evidence), {'submission', 'workspace'})
            audit = json.loads(Path(root, '.mad/audit/project-review.json').read_text())
            self.assertTrue(audit['accepted'])
            self.assertEqual(audit['response']['assessments'][0]['criterion'], criterion)

    async def test_disconnected_contribution_is_revised_before_acceptance(self):
        with tempfile.TemporaryDirectory() as root:
            # A writing fixture exercises the same integration failure as an unused implementation.
            Path(root, 'report.md').write_text('# Report\nTODO: include analysis')
            Path(root, 'analysis.md').write_text('The observed difference is uncertain.')
            criterion = 'The final report incorporates the analysis'
            project = Project(criterion, root, success=[criterion], agents=team()[:2])
            supervisor = ScriptedModel([
                {'tasks': [dict(task_data(), acceptance=[criterion])]},
                {'reason': 'Inspect the actual report', 'actions': [],
                 'tool_calls': [{'tool': 'read', 'path': 'report.md'}]},
                {'reason': 'The report is a placeholder; the separate analysis is not integrated.',
                 'actions': [{'type': 'revise', 'task_id': 'T1',
                              'updates': {'description': 'Integrate analysis.md into report.md and verify it.'}}]},
                {'reason': 'Read the corrected deliverable', 'actions': [],
                 'tool_calls': [{'tool': 'read', 'path': 'report.md'}]},
                {'reason': 'The report now includes the analysis',
                 'actions': [accept(criterion=criterion, evidence='review_1')]},
                {'reason': 'Fresh final inspection', 'actions': [], 'accepted': False, 'assessments': [],
                 'tool_calls': [{'tool': 'read', 'path': 'report.md'}]},
                {'reason': 'Current integrated report satisfies the objective', 'accepted': True,
                 'actions': [], 'assessments': [assessment(criterion, 'review_1')]},
            ])
            worker = ScriptedModel([
                {'state': 'final'},
                {'status': 'completed', 'summary': 'Everything is integrated', 'output': 'report.md', 'messages': []},
                {'state': 'tool'},
                {'tool_calls': [
                    {'tool': 'read', 'path': 'report.md'},
                    {'tool': 'read', 'path': 'analysis.md'},
                    {'tool': 'write', 'path': 'report.md', 'content': '# Report\nThe observed difference is uncertain.'},
                ], 'messages': []},
                {'state': 'final'},
                {'status': 'completed', 'summary': 'Integrated the analysis', 'output': 'report.md', 'messages': []},
            ])
            runtime = Runtime(project, model_factory=lambda agent: supervisor if agent.name == 'Supervisor' else worker,
                              reporter=reporter())
            await asyncio.wait_for(runtime.run(), 5)
            self.assertTrue(runtime.project_accepted)
            decisions = [json.loads(m.content) for m in runtime.hub.history() if m.type == 'REPLAN_RESULT']
            self.assertEqual([d['actions'][0]['type'] for d in decisions], ['revise', 'accept'])
            self.assertIn('TODO', supervisor.calls[2]['messages'][-1]['content'])
            self.assertIn('placeholder', worker.calls[2]['messages'][1]['content'])
            audit = json.loads(Path(root, '.mad/audit/T1-review.json').read_text())
            self.assertTrue(audit['accepted'])
            history = [json.loads(line) for line in Path(root, '.mad/audit/T1-review.jsonl').read_text().splitlines()]
            self.assertTrue(any(record['response'].get('reason', '').startswith('The report is a placeholder')
                                for record in history))
            self.assertIn('observed difference', audit['evidence']['review_1']['result'])

    async def test_final_artifact_review_rejects_only_stale_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            project = Project('Assess current report', root, agents=team())
            runtime = Runtime(project, reporter=reporter())
            runtime._find_supervisor()
            runtime.state.install_plan({'tasks': [task_data()]})
            project.tasks[0].status = 'completed'
            runtime.project_review_pending = True
            runtime.project_evidence = {
                'workspace': {'success': True, 'result': 'report.md'},
                'T1:submission': {'output': 'An earlier claim'},
            }
            await runtime._handle_project_review_result(Message(
                'Supervisor', '__runtime__', 'PROJECT_REVIEW_RESULT',
                content=json.dumps({'accepted': True, 'actions': [], 'reason': 'Looks fine',
                                    'assessments': [assessment(project.objective, 'T1:submission')]}),
            ))
            self.assertFalse(runtime.project_accepted)
            audit = json.loads(Path(root, '.mad/audit/project-review.json').read_text())
            self.assertIn('fresh inspection', audit['error'])

    async def test_invalid_decision_is_corrected_by_supervisor_without_rerunning_worker(self):
        with tempfile.TemporaryDirectory() as root:
            project = Project('Goal', root, agents=team())
            runtime = Runtime(project, reporter=reporter())
            runtime._find_supervisor()
            runtime.hub.register('Supervisor')
            runtime.state.install_plan({'tasks': [task_data()]})
            runtime.state.start(project.tasks[0])
            runtime.state.record_result('T1', 'Worker', 'completed')
            runtime.state.evidence['T1'] = {'submission': {'output': 'Actual deliverable'}}
            runtime.pending_reviews.add('T1')
            await runtime._handle_replan_result(Message(
                'Supervisor', '__runtime__', 'REPLAN_RESULT', task_id='T1',
                content=json.dumps({'reason': 'Claim without assessment',
                                    'actions': [{'type': 'accept', 'task_id': 'T1'}]}),
            ))
            self.assertEqual(project.tasks[0].status, 'review')
            self.assertEqual(runtime.pending_reviews, {'T1'})
            correction = await runtime.hub.receive('Supervisor')
            self.assertEqual(correction.type, 'REPLAN_REQUEST')
            self.assertIn('assessment for every criterion', correction.content)
            self.assertFalse(any(m.type == 'TASK' for m in runtime.hub.history()))
            await runtime._handle_replan_result(Message(
                'Supervisor', '__runtime__', 'REPLAN_RESULT', task_id='T1',
                content=json.dumps({'reason': 'Assessed actual output', 'actions': [accept()]}),
            ))
            self.assertEqual(project.tasks[0].status, 'completed')
            self.assertFalse(runtime.pending_reviews)

    async def test_invalid_supervisor_corrections_stop_without_accepting_work(self):
        with tempfile.TemporaryDirectory() as root:
            project = Project('Goal', root, agents=team())
            runtime = Runtime(project, reporter=reporter())
            runtime._find_supervisor()
            runtime.hub.register('Supervisor')
            runtime.state.install_plan({'tasks': [task_data()]})
            runtime.state.start(project.tasks[0])
            runtime.state.record_result('T1', 'Worker', 'completed')
            runtime.pending_reviews.add('T1')
            message = Message('Supervisor', '__runtime__', 'REPLAN_RESULT', task_id='T1',
                              content=json.dumps({'actions': [], 'reason': 'No decision'}))
            await runtime._handle_replan_result(message)
            with self.assertRaisesRegex(RuntimeError, 'remained invalid after correction'):
                await runtime._handle_replan_result(message)
            self.assertEqual(project.tasks[0].status, 'review')
            self.assertFalse(runtime.project_accepted)

    async def test_cancelled_final_review_never_approves_project(self):
        with tempfile.TemporaryDirectory() as root:
            project = Project('Explain tradeoff', root, agents=team()[:2])
            supervisor = ScriptedModel([
                {'tasks': [dict(task_data(), acceptance=['Explain tradeoff'])]},
                {'reason': 'Evaluated the explanation',
                 'actions': [accept(criterion='Explain tradeoff')]},
            ])
            worker = ScriptedModel([
                {'state': 'final'},
                {'status': 'completed', 'summary': 'Explained',
                 'output': 'The faster option costs more.', 'messages': []},
            ])
            runtime = Runtime(project, model_factory=lambda agent: supervisor if agent.name == 'Supervisor' else worker,
                              reporter=reporter())
            final_started = asyncio.Event()
            review = SupervisorProtocol.review

            async def waiting_review(protocol, message):
                if message.type == 'PROJECT_REVIEW_REQUEST':
                    final_started.set()
                    await asyncio.Future()
                else:
                    await review(protocol, message)

            with patch.object(SupervisorProtocol, 'review', waiting_review):
                running = asyncio.create_task(runtime.run())
                try:
                    await asyncio.wait_for(final_started.wait(), 5)
                finally:
                    running.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await running
            self.assertEqual(project.tasks[0].status, 'completed')
            self.assertFalse(runtime.project_accepted)
            self.assertTrue(all(task.done() for task in runtime.runtime_tasks))

    async def test_final_review_can_reopen_completed_work(self):
        with tempfile.TemporaryDirectory() as root:
            project = Project('Complete report', root, agents=team())
            runtime = Runtime(project, reporter=reporter())
            runtime._find_supervisor()
            runtime.hub.register('Worker')
            runtime.state.install_plan({'tasks': [task_data()]})
            project.tasks[0].status = 'completed'
            runtime.project_review_pending = True
            runtime.project_review_attempted = True
            await runtime._handle_project_review_result(Message(
                'Supervisor', '__runtime__', 'PROJECT_REVIEW_RESULT',
                content=json.dumps({'accepted': False, 'reason': 'Missing synthesis',
                                    'assessments': [], 'actions': [{'type': 'reopen', 'task_id': 'T1'}]}),
            ))
            self.assertFalse(runtime.project_accepted)
            self.assertFalse(runtime.project_review_attempted)
            task = await runtime.hub.receive('Worker')
            self.assertIn('Missing synthesis', task.metadata['review_feedback'])
            self.assertEqual(project.tasks[0].status, 'running')
