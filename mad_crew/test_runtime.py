import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mad.agents import Intelligence
from .__main__ import project
from .contract import Contract, workspace_path
from .runtime import Runtime


def contract(task='Produce a result.', files=(), data=None, inputs=(), setting='You are an expert writer.'):
    return {'SETTING': setting, 'INPUT': {'files': list(inputs), 'data': {}}, 'TASK': task,
            'OUTPUT': {'files': list(files), 'data': data or {}}, 'ACCEPTANCE': ['The result meets the requested objective.']}


def worker_packet(messages):
    marker = '\nCurrent contract:\n'
    return json.loads(messages[0]['content'].split(marker, 1)[1]) if marker in messages[0]['content'] else None


def inspection_checks(value):
    return [dict(criterion=index, method='inspection', result='pass',
                 details='Inspected the supplied test artifact or inline value.', runs=[])
            for index, _ in enumerate(value['ACCEPTANCE'], 1)]


def response(action, messages):
    action = dict(action)
    packet = worker_packet(messages)
    if packet and 'output' in action and 'checks' not in action:
        action['checks'] = inspection_checks(packet['contract'])
    if 'review' in action and action['review'].get('decision') == 'accept' and 'checks' not in action['review']:
        events = (json.loads(message['content']) for message in reversed(messages) if message['role'] == 'user')
        event = next(event for event in events if event.get('event') == 'delivery')
        action['review'] = {**action['review'], 'checks': inspection_checks(event['contract'])}
    return json.dumps(action)


class ContractTests(unittest.TestCase):
    def test_five_section_round_trip_with_files_inline_data_and_acceptance(self):
        value = contract(files=['report.md'], data={'summary': 'brief text'}, inputs=['notes.txt'])
        value['INPUT']['data'] = {'audience': 'new readers', 'count': 3}
        value['ACCEPTANCE'] = ['Cites the supplied notes.', 'States uncertainty.\nExplains the limitations.']
        parsed = Contract.from_dict(value)
        self.assertEqual(Contract.from_markdown(parsed.markdown()).to_dict(), value)
        self.assertEqual(parsed.outputs, {'files': ['report.md'], 'data': {'summary': 'brief text'}})
        self.assertIn('# ACCEPTANCE\n\n- Cites', parsed.markdown())

    def test_invalid_structures_do_not_pass_as_contracts(self):
        base = contract(data={'answer': 'text'})
        values = [{k: v for k, v in base.items() if k != 'ACCEPTANCE'},
                  {**base, 'SETTING': ''}, {**base, 'TASK': None}, {**base, 'ACCEPTANCE': []},
                  {**base, 'ACCEPTANCE': 'good'}, {**base, 'OUTPUT': {'files': [], 'data': {}}},
                  {**base, 'INPUT': {'files': 'source.md', 'data': {}}},
                  {**base, 'OUTPUT': {'files': ['a', 'a'], 'data': {}}}]
        for value in values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                Contract.from_dict(value)
        with self.assertRaises(ValueError):
            Contract.from_markdown('# SETTING\nOnly one section')

    def test_paths_cannot_escape_workspace_or_use_audit_as_artifacts(self):
        with tempfile.TemporaryDirectory() as root:
            for name in ('../outside', '/tmp/outside', '.mad/audit/log.md'):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    workspace_path(root, name)
            self.assertEqual(workspace_path(root, '././report.md'), Path(root, 'report.md'))

    def test_project_keeps_markdown_format_without_loading_legacy_roles(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, 'project.md')
            path.write_text('Write a report.\nWORKSPACE:\n./report\nSUCCESS:\n- Clear sources.\nAGENTS:\n- Legacy (Missing.md:model)\n')
            task, workspace = project(path)
        self.assertIn('Write a report.', task)
        self.assertIn('Clear sources.', task)
        self.assertNotIn('Legacy', task)
        self.assertEqual(workspace, './report')


class PlanTests(unittest.TestCase):
    def test_cycles_unknown_dependencies_and_duplicate_ids_are_rejected(self):
        definitions = [
            [{'id': 'a', 'depends': ['a'], 'contract': contract(data={'answer': 'text'})}],
            [{'id': 'a', 'depends': ['missing'], 'contract': contract(data={'answer': 'text'})}],
            [{'id': 'a', 'depends': ['b'], 'contract': contract(data={'answer': 'text'})},
             {'id': 'b', 'depends': ['a'], 'contract': contract(data={'answer': 'text'})}],
            [{'id': 'a', 'depends': [], 'contract': contract(data={'answer': 'text'})}] * 2,
        ]
        with tempfile.TemporaryDirectory() as root:
            for definition in definitions:
                with self.subTest(definition=definition), self.assertRaises(ValueError):
                    Runtime('Goal', root, 'http://model/v1', 'test').install_plan(definition, 1)

    def test_worker_limit_and_output_ownership_are_enforced(self):
        definitions = [{'id': name, 'depends': [], 'contract': contract(files=['shared.md'])} for name in ('a', 'b')]
        with tempfile.TemporaryDirectory() as root:
            runtime = Runtime('Goal', root, 'http://model/v1', 'test', workers=2)
            with self.assertRaisesRegex(ValueError, 'between 1 and 2'):
                runtime.install_plan(definitions, 3)
            with self.assertRaisesRegex(ValueError, 'same files'):
                runtime.install_plan(definitions, 2)
            definitions[1]['depends'] = ['a']
            definitions[1]['contract']['INPUT']['files'] = ['shared.md']
            runtime.install_plan(definitions, 2)
            self.assertEqual(runtime.packages['b']['state'], 'pending')

    def test_missing_inputs_must_be_supplied_by_a_prerequisite(self):
        definitions = [{'id': 'a', 'depends': [], 'contract': contract(files=['result.md'], inputs=['missing.md'])}]
        with tempfile.TemporaryDirectory() as root:
            runtime = Runtime('Goal', root, 'http://model/v1', 'test')
            with self.assertRaisesRegex(ValueError, 'not supplied'):
                runtime.install_plan(definitions, 1)
            definitions[0]['contract']['INPUT']['files'] = []
            definitions[0]['contract']['OUTPUT']['files'] = ['../outside.md']
            with self.assertRaisesRegex(ValueError, 'stay inside'):
                runtime.install_plan(definitions, 1)


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_work_waits_for_acceptance_and_hands_inline_results_onward(self):
        plan = [
            {'id': 'left', 'depends': [], 'contract': contract(data={'fact': 'text'}, setting='You are an expert on left.')},
            {'id': 'right', 'depends': [], 'contract': contract(data={'fact': 'text'}, setting='You are an expert on right.')},
            {'id': 'combine', 'depends': ['left', 'right'],
             'contract': contract(files=['combined.txt'], data={'joined': 'text'}, setting='You are an expert synthesizer.')},
        ]
        ready = asyncio.Event()
        started, reviewed = set(), set()
        calls = {}

        async def reply(intelligence, messages, **kwargs):
            packet = worker_packet(messages)
            if packet:
                name = packet['package']
                self.assertNotIn('PRIVATE GLOBAL OBJECTIVE', json.dumps(messages))
                calls[name] = calls.get(name, 0) + 1
                if calls[name] == 1:
                    self.assertEqual(len(messages), 2)
                if name in ('left', 'right'):
                    started.add(name)
                    if len(started) == 2:
                        ready.set()
                    await asyncio.wait_for(ready.wait(), 1)
                    return response({'output': {'files': [], 'data': {'fact': name}}}, messages)
                self.assertEqual(reviewed, {'left', 'right'})
                self.assertEqual(packet['contract']['SETTING'], 'You are an expert synthesizer.')
                self.assertNotIn('expert on left', messages[0]['content'])
                self.assertNotIn('expert on right', messages[0]['content'])
                dependencies = packet['contract']['INPUT']['data']['dependencies']
                self.assertEqual(dependencies['left']['data']['fact'], 'left')
                if calls[name] == 1:
                    return response({'python': "from pathlib import Path; Path('combined.txt').write_text('left + right'); print('created comparison')"}, messages)
                return response({'output': {'files': ['combined.txt'], 'data': {'joined': 'left + right'}}}, messages)
            event = json.loads(messages[-1]['content'])
            self.assertIn('PRIVATE GLOBAL OBJECTIVE', messages[0]['content'])
            if event['event'] == 'project':
                return response({'plan': plan, 'workers': 2}, messages)
            if event['event'] == 'delivery':
                reviewed.add(event['package'])
                if event['package'] == 'combine':
                    self.assertEqual(event['artifacts'][0]['text'], 'left + right')
                    self.assertEqual(event['executions'][0]['returncode'], 0)
                return response({'review': {'package': event['package'], 'revision': event['revision'],
                                              'decision': 'accept', 'reason': 'Inspected the supplied output.'}}, messages)
            return response({'finish': {'accepted': True, 'reason': 'Accepted both sources and their comparison.'}}, messages)

        with tempfile.TemporaryDirectory() as root:
            runtime = Runtime('PRIVATE GLOBAL OBJECTIVE', root, 'http://model/v1', 'test', workers=2)
            with patch.object(Intelligence, 'reply', reply), contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(await asyncio.wait_for(runtime.run(), 3))
            self.assertEqual(Path(root, 'combined.txt').read_text(), 'left + right')
            self.assertTrue(all(p['state'] == 'accepted' for p in runtime.packages.values()))
            stored = Contract.from_markdown((runtime.contracts / 'combine-r1.md').read_text())
            self.assertEqual(stored.inputs['data']['dependencies']['right']['data']['fact'], 'right')
            events = [json.loads(line) for line in runtime.log.read_text().splitlines()]
            accepted_parents = [i for i, e in enumerate(events) if e['event'] == 'supervisor_review' and e['package'] in ('left', 'right')]
            child_dispatch = next(i for i, e in enumerate(events) if e['event'] == 'contract_sent' and e['package'] == 'combine')
            self.assertGreater(child_dispatch, max(accepted_parents))
            self.assertIn('ACCEPTANCE', runtime.log.with_suffix('.md').read_text())
            self.assertTrue((Path(root, '.mad') / f'plan-{runtime.run_id}.json').is_file())

    async def test_rejection_issues_a_new_revision_and_fresh_worker_context(self):
        initial = contract(files=['draft.txt'], setting='You are an expert writer.')
        revised = contract(task='Correct draft.txt to final.', files=['draft.txt'], inputs=['draft.txt'],
                           setting='You are an expert editor.')
        calls = {}

        async def reply(intelligence, messages, **kwargs):
            packet = worker_packet(messages)
            if packet:
                revision = packet['revision']
                calls[revision] = calls.get(revision, 0) + 1
                if calls[revision] == 1:
                    self.assertEqual(len(messages), 2)
                    if revision == 2:
                        self.assertNotIn('expert writer', messages[0]['content'])
                        self.assertIn('expert editor', messages[0]['content'])
                    return response({'python': f"from pathlib import Path; Path('draft.txt').write_text({'bad' if revision == 1 else 'final'!r})"}, messages)
                return response({'output': {'files': ['draft.txt'], 'data': {}}}, messages)
            event = json.loads(messages[-1]['content'])
            if event['event'] == 'project':
                return response({'plan': [{'id': 'draft', 'depends': [], 'contract': initial}], 'workers': 1}, messages)
            if event['event'] == 'delivery':
                if event['revision'] == 1:
                    self.assertEqual(event['artifacts'][0]['text'], 'bad')
                    return response({'review': {'package': 'draft', 'revision': 1, 'decision': 'reject',
                                                  'reason': 'The draft needs correction.', 'contract': revised}}, messages)
                self.assertEqual(event['artifacts'][0]['text'], 'final')
                return response({'review': {'package': 'draft', 'revision': 2, 'decision': 'accept', 'reason': 'Corrected output verified.'}}, messages)
            return response({'finish': {'accepted': True, 'reason': 'Accepted the corrected draft.'}}, messages)

        with tempfile.TemporaryDirectory() as root:
            runtime = Runtime('Write a corrected draft.', root, 'http://model/v1', 'test', workers=1)
            with patch.object(Intelligence, 'reply', reply), contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(await asyncio.wait_for(runtime.run(), 3))
            self.assertEqual(Path(root, 'draft.txt').read_text(), 'final')
            self.assertEqual(Contract.from_markdown((runtime.contracts / 'draft-r1.md').read_text()).setting, initial['SETTING'])
            self.assertEqual(Contract.from_markdown((runtime.contracts / 'draft-r2.md').read_text()).setting, revised['SETTING'])
            self.assertEqual(runtime.packages['draft']['revision'], 2)

    async def test_questions_are_routed_through_supervisor_and_not_counted_as_output(self):
        calls = 0

        async def reply(intelligence, messages, **kwargs):
            nonlocal calls
            packet = worker_packet(messages)
            if packet:
                calls += 1
                if calls == 1:
                    return response({'question': {'files': [], 'data': {'question': 'Which word should I use?'}}}, messages)
                self.assertIn('hello', messages[-1]['content'])
                return response({'output': {'files': [], 'data': {'word': 'hello'}}}, messages)
            event = json.loads(messages[-1]['content'])
            if event['event'] == 'project':
                return response({'plan': [{'id': 'answer', 'depends': [], 'contract': contract(data={'word': 'text'})}], 'workers': 1}, messages)
            if event.get('type') == 'question':
                self.assertEqual(event['plan']['answer']['state'], 'running')
                return response({'answer': {'files': [], 'data': {'answer': 'Use hello.'}}}, messages)
            if event['event'] == 'delivery':
                return response({'review': {'package': 'answer', 'revision': 1, 'decision': 'accept', 'reason': 'Correct answer.'}}, messages)
            return response({'finish': {'accepted': True, 'reason': 'Resolved the question.'}}, messages)

        with tempfile.TemporaryDirectory() as root:
            with patch.object(Intelligence, 'reply', reply), contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(await asyncio.wait_for(Runtime('Answer a question.', root, 'http://model/v1', 'test').run(), 3))

    async def test_failed_worker_can_end_honestly_without_false_acceptance(self):
        async def reply(intelligence, messages, **kwargs):
            if worker_packet(messages):
                raise RuntimeError('Model connection failed')
            event = json.loads(messages[-1]['content'])
            if event['event'] == 'project':
                return response({'plan': [{'id': 'work', 'depends': [], 'contract': contract(data={'answer': 'text'})}], 'workers': 1}, messages)
            self.assertIn('Model connection failed', event['error'])
            return response({'finish': {'accepted': False, 'reason': 'Worker could not complete the contract.'}}, messages)

        with tempfile.TemporaryDirectory() as root:
            runtime = Runtime('Work.', root, 'http://model/v1', 'test')
            with patch.object(Intelligence, 'reply', reply), contextlib.redirect_stdout(io.StringIO()):
                self.assertFalse(await asyncio.wait_for(runtime.run(), 3))
            events = [json.loads(line) for line in runtime.log.read_text().splitlines()]
            self.assertFalse(events[-1]['accepted'])
            self.assertEqual(runtime.packages['work']['state'], 'review')

    async def test_missing_outputs_fail_and_execution_evidence_is_not_fabricated(self):
        actions = iter([{'output': {'files': ['result.txt'], 'data': {}}},
                        {'python': "from pathlib import Path; Path('result.txt').write_text('actual'); print('real failure'); raise SystemExit(2)"},
                        {'output': {'files': ['result.txt'], 'data': {}}}])

        async def reply(intelligence, messages, **kwargs):
            if worker_packet(messages):
                return response(next(actions), messages)
            event = json.loads(messages[-1]['content'])
            if event['event'] == 'project':
                return response({'plan': [{'id': 'produce', 'depends': [], 'contract': contract(files=['result.txt'])}], 'workers': 1}, messages)
            if event['event'] == 'delivery':
                self.assertEqual(event['executions'][0]['returncode'], 2)
                self.assertEqual(event['executions'][0]['stdout'], 'real failure\n')
                self.assertEqual(event['artifacts'][0]['text'], 'actual')
                return response({'review': {'package': 'produce', 'revision': 1, 'decision': 'accept',
                                              'reason': 'The requested artifact is present; the separate failed command remains recorded.'}}, messages)
            return response({'finish': {'accepted': True, 'reason': 'Reviewed the artifact and execution limitation.'}}, messages)

        with tempfile.TemporaryDirectory() as root:
            runtime = Runtime('Produce an artifact.', root, 'http://model/v1', 'test')
            with patch.object(Intelligence, 'reply', reply), contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(await asyncio.wait_for(runtime.run(), 3))
            events = [json.loads(line) for line in runtime.log.read_text().splitlines()]
            self.assertTrue(any(e['event'] == 'action_failed' and 'does not exist' in e['error'] for e in events))
            execution = next(e for e in events if e['event'] == 'python_finished')
            self.assertEqual(execution['returncode'], 2)
            self.assertEqual(execution['stdout'], 'real failure\n')

    async def test_authority_and_premature_finish_are_enforced(self):
        package = contract(data={'answer': 'text'})
        supervisor = iter([{'finish': {'accepted': True, 'reason': 'Too early'}},
                           {'plan': [{'id': 'a', 'depends': [], 'contract': package}], 'workers': 1},
                           {'python': "raise AssertionError('Supervisor must not run this')"},
                           {'review': {'package': 'a', 'revision': 99, 'decision': 'accept', 'reason': 'Stale'}},
                           {'finish': {'accepted': True, 'reason': 'Still not accepted'}},
                           {'review': {'package': 'a', 'revision': 1, 'decision': 'accept', 'reason': 'Reviewed'}},
                           {'finish': {'accepted': True, 'reason': 'Now accepted'}}])
        worker = iter([{'finish': {'accepted': True, 'reason': 'Worker is not Supervisor'}},
                       {'output': {'files': [], 'data': {'answer': 'actual answer'}}}])

        async def reply(intelligence, messages, **kwargs):
            return response(next(worker if worker_packet(messages) else supervisor), messages)

        with tempfile.TemporaryDirectory() as root:
            runtime = Runtime('Answer.', root, 'http://model/v1', 'test')
            with patch.object(Intelligence, 'reply', reply), contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(await asyncio.wait_for(runtime.run(), 3))
            events = [json.loads(line) for line in runtime.log.read_text().splitlines()]
            self.assertEqual(sum(e['event'] == 'action_failed' for e in events), 5)
            self.assertFalse(any(e['event'] == 'python_started' and e['agent'] == 'Supervisor' for e in events))

    async def test_cancelled_execution_is_reaped(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = Runtime('Check cancellation.', root, 'http://model/v1', 'test')
            agent = runtime.supervisor
            code = "import os,time; from pathlib import Path; Path('pid.txt').write_text(str(os.getpid())); time.sleep(20)"
            with contextlib.redirect_stdout(io.StringIO()):
                running = asyncio.create_task(runtime.python(agent, 'test', 1, code))
                try:
                    for _ in range(100):
                        if Path(root, 'pid.txt').exists():
                            break
                        await asyncio.sleep(0.01)
                    pid = int(Path(root, 'pid.txt').read_text())
                finally:
                    running.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await running
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)


class CheckTests(unittest.IsolatedAsyncioTestCase):
    def test_check_reports_cover_each_condition_and_reference_actual_runs(self):
        value = contract(data={'answer': 'text'})
        value['ACCEPTANCE'] = ['First condition.', 'Second condition.']
        current = Contract.from_dict(value)
        valid = inspection_checks(value)
        valid[0].update(method='execution', runs=[1])
        executions = [dict(run=1, returncode=0, timed_out=False)]
        with tempfile.TemporaryDirectory() as root:
            runtime = Runtime('Check reports.', root, 'http://model/v1', 'test')
            self.assertEqual(runtime.checks(valid, current, executions), valid)
            invalid = [None, [], [valid[0], valid[0]],
                       [{**valid[0], 'runs': []}, valid[1]],
                       [{**valid[0], 'runs': [2]}, valid[1]],
                       [{**valid[0], 'runs': [True]}, valid[1]],
                       [{**valid[0], 'method': 'none'}, valid[1]],
                       [{**valid[0], 'details': ''}, valid[1]]]
            for checks in invalid:
                with self.subTest(checks=checks), self.assertRaises(ValueError):
                    runtime.checks(checks, current, executions)
            for run in (dict(returncode=1, timed_out=False), dict(returncode=0, timed_out=True)):
                with self.subTest(run=run), self.assertRaisesRegex(ValueError, 'failed or timed-out'):
                    runtime.checks(valid, current, [run])

    async def test_failed_or_unverified_checks_block_acceptance_and_dependents(self):
        value = contract(data={'answer': 'text'})
        with tempfile.TemporaryDirectory() as root:
            runtime = Runtime('Check work.', root, 'http://model/v1', 'test')
            runtime.install_plan([
                {'id': 'work', 'depends': [], 'contract': value},
                {'id': 'after', 'depends': ['work'], 'contract': contract(data={'summary': 'text'})},
            ], 1)
            package = runtime.packages['work']
            package['state'] = 'review'
            package['result'] = dict(error=None, output={'files': [], 'data': {'answer': 'observed'}},
                                     executions=[], checks=inspection_checks(value))
            decision = dict(package='work', revision=1, decision='accept', reason='Review the evidence.')
            with self.assertRaisesRegex(ValueError, 'Accept requires checks'):
                runtime.review(decision, 'work')
            decision['checks'] = inspection_checks(value)
            package['result']['checks'][0].update(result='unverified', method='none', details='No check performed.')
            with self.assertRaisesRegex(ValueError, 'Failed or unverified'):
                runtime.review(decision, 'work')
            package['result']['checks'][0].update(result='fail', method='inspection', details='Condition is not met.')
            with self.assertRaisesRegex(ValueError, 'Failed or unverified'):
                runtime.review(decision, 'work')
            package['result']['checks'] = inspection_checks(value)
            decision['checks'][0].update(result='unverified', method='none')
            with self.assertRaisesRegex(ValueError, 'Failed or unverified'):
                runtime.review(decision, 'work')
            with contextlib.redirect_stdout(io.StringIO()):
                await runtime.dispatch()
            self.assertEqual(runtime.packages['after']['state'], 'pending')
            self.assertTrue(runtime.bus.inboxes['Worker-1'].empty())
            decision['checks'] = inspection_checks(value)
            runtime.review(decision, 'work')
            with contextlib.redirect_stdout(io.StringIO()):
                await runtime.dispatch()
            self.assertEqual(runtime.packages['after']['state'], 'running')

    def test_revisions_may_expand_scope_but_keep_handoffs_and_ownership_valid(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = Runtime('Correct an artifact.', root, 'http://model/v1', 'test')
            runtime.install_plan([
                {'id': 'work', 'depends': [], 'contract': contract(files=['main.txt'])},
                {'id': 'after', 'depends': ['work'], 'contract': contract(files=['report.txt'], inputs=['main.txt'])},
                {'id': 'other', 'depends': [], 'contract': contract(files=['owned.txt'])},
            ], 1)
            runtime.packages['work'].update(state='review', result=dict(error=None, executions=[]))
            decision = dict(package='work', revision=1, decision='reject', reason='Correction needs an additional file.')
            with self.assertRaisesRegex(ValueError, 'retain existing'):
                runtime.review({**decision, 'contract': contract(files=['replacement.txt'])}, 'work')
            with self.assertRaisesRegex(ValueError, 'same files'):
                runtime.review({**decision, 'contract': contract(files=['main.txt', 'owned.txt'])}, 'work')
            runtime.review({**decision, 'contract': contract(files=['main.txt', 'index.txt'])}, 'work')
            package = runtime.packages['work']
            self.assertEqual(package['revision'], 2)
            self.assertEqual(package['state'], 'pending')
            self.assertEqual(package['contract'].outputs['files'], ['main.txt', 'index.txt'])
            self.assertEqual(runtime.packages['after']['depends'], ['work'])

    def test_copied_dependency_metadata_is_replaced_with_accepted_output(self):
        source = contract(data={'fact': 'text'})
        target = contract(data={'summary': 'text'})
        target['INPUT']['data']['dependencies'] = {'source': {'data': {'fact': 'fabricated'}}}
        with tempfile.TemporaryDirectory() as root:
            runtime = Runtime('Summarize accepted evidence.', root, 'http://model/v1', 'test')
            runtime.install_plan([{'id': 'source', 'depends': [], 'contract': source},
                                  {'id': 'target', 'depends': ['source'], 'contract': target}], 1)
            self.assertNotIn('dependencies', runtime.packages['target']['contract'].inputs['data'])
            runtime.packages['source'].update(state='accepted', result={'output': {'files': [], 'data': {'fact': 'actual'}}})
            actual = runtime.resolved_contract('target')
            self.assertEqual(actual.inputs['data']['dependencies']['source']['data']['fact'], 'actual')

    async def test_workers_must_return_checks_alongside_artifacts(self):
        calls = 0
        value = contract(data={'answer': 'text'})

        async def reply(intelligence, messages, **kwargs):
            nonlocal calls
            if worker_packet(messages):
                calls += 1
                if calls == 1:
                    return json.dumps({'output': {'files': [], 'data': {'answer': 'observed'}}})
                self.assertIn('one check for every ACCEPTANCE', messages[-1]['content'])
                return json.dumps({'output': {'files': [], 'data': {'answer': 'observed'}}, 'checks': inspection_checks(value)})
            event = json.loads(messages[-1]['content'])
            if event['event'] == 'project':
                return json.dumps({'plan': [{'id': 'work', 'depends': [], 'contract': value}], 'workers': 1})
            if event['event'] == 'delivery':
                self.assertEqual(len(event['worker_checks']), 1)
                return json.dumps({'review': {'package': 'work', 'revision': 1, 'decision': 'accept',
                    'reason': 'Inspected actual inline output.', 'checks': inspection_checks(value)}})
            return json.dumps({'finish': {'accepted': True, 'reason': 'Checks and output reviewed.'}})

        with tempfile.TemporaryDirectory() as root:
            runtime = Runtime('Review a statement.', root, 'http://model/v1', 'test')
            with patch.object(Intelligence, 'reply', reply), contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(await asyncio.wait_for(runtime.run(), 3))
            events = [json.loads(line) for line in runtime.log.read_text().splitlines()]
            self.assertEqual(sum(e['event'] == 'action_failed' for e in events), 1)
            self.assertEqual(next(e for e in events if e['event'] == 'worker_output')['checks'], inspection_checks(value))

    async def test_supervisor_can_inspect_numbered_execution_evidence(self):
        value = contract(data={'answer': 'text'})
        with tempfile.TemporaryDirectory() as root:
            runtime = Runtime('Review evidence.', root, 'http://model/v1', 'test')
            runtime.install_plan([{'id': 'work', 'depends': [], 'contract': value}], 1)
            with contextlib.redirect_stdout(io.StringIO()):
                run = await runtime.python(runtime.workers['Worker-1'], 'work', 1, "print('actual observation')")
            checks = inspection_checks(value)
            checks[0].update(method='execution', runs=[1])
            runtime.packages['work'].update(state='review', result=dict(error=None, executions=[run],
                output={'files': [], 'data': {'answer': 'observed'}}, checks=checks))
            actions = iter([{'execution': 1}, {'review': {'package': 'work', 'revision': 1, 'decision': 'accept',
                'reason': 'Reviewed the actual run.', 'checks': checks}}])
            calls = 0

            async def reply(intelligence, messages, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    evidence = json.loads(messages[-1]['content'])
                    self.assertEqual(evidence['run'], 1)
                    self.assertEqual(evidence['stdout'], 'actual observation\n')
                    self.assertEqual(evidence['returncode'], 0)
                return json.dumps(next(actions))

            with patch.object(Intelligence, 'reply', reply), contextlib.redirect_stdout(io.StringIO()):
                await runtime.supervisor_turn({'event': 'delivery', 'package': 'work', 'revision': 1}, 'review')
            self.assertEqual(runtime.packages['work']['state'], 'accepted')
            events = [json.loads(line) for line in runtime.log.read_text().splitlines()]
            self.assertTrue(any(e['event'] == 'execution_read' for e in events))
