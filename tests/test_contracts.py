import asyncio
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mad.agents import Agent, Bus, Intelligence
from mad.runtime import read_contract, start, work


class ContractTests(unittest.IsolatedAsyncioTestCase):
    def test_contract_has_input_task_and_artifact_paths_without_acceptance_fields(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, 'source.txt').write_text('Research source')
            Path(root, 'contract.md').write_text('# INPUT\n- ./source.txt\n\n# TASK\nSummarize the source without adding claims.\n\n# OUTPUT\n- summary.txt\n')
            contract = read_contract(root, 'contract.md')
        self.assertEqual(contract, {'path': 'contract.md', 'INPUT': ['source.txt'],
                                   'TASK': 'Summarize the source without adding claims.', 'OUTPUT': ['summary.txt']})

    def test_invalid_contracts_are_rejected(self):
        cases = [
            '# TASK\nWork\n# OUTPUT\n- result.txt\n',
            '# INPUT\n# OUTPUT\n- result.txt\n# TASK\nWork\n',
            '# INPUT\n# INPUT\n# TASK\nWork\n# OUTPUT\n- result.txt\n',
            '# INPUT\n# TASK\n# OUTPUT\n- result.txt\n',
            '# INPUT\n# TASK\nWork\n# OUTPUT\n',
            '# INPUT\n- missing.txt\n# TASK\nWork\n# OUTPUT\n- result.txt\n',
            '# INPUT\n# TASK\nWork\n# OUTPUT\nThe report must be excellent.\n',
            '# INPUT\n# TASK\nWork\n# OUTPUT\n- ../outside.txt\n',
            '# INPUT\n# TASK\nWork\n# OUTPUT\n- .mad/audit/result.md\n',
        ]
        with tempfile.TemporaryDirectory() as root:
            for text in cases:
                with self.subTest(text=text):
                    Path(root, 'contract.md').write_text(text)
                    with self.assertRaises(ValueError):
                        read_contract(root, 'contract.md')

    async def test_missing_contract_is_reported_before_future_output_attachment(self):
        actions = iter([{'to': 'Worker', 'text': 'Begin.', 'contract': 'contract.md', 'files': ['future.txt']},
                        {'done': 'No contract was issued.'}])
        prompts = []

        async def reply(intelligence, messages, **kwargs):
            self.assertTrue(messages[0]['content'].startswith('You are Supervisor.'))
            prompts.append(messages[-1]['content'])
            return json.dumps(next(actions))

        with tempfile.TemporaryDirectory() as root:
            with patch.object(Intelligence, 'reply', reply), contextlib.redirect_stdout(io.StringIO()):
                await asyncio.wait_for(start('Discuss a contract.', {'Supervisor': 'Coordinate.', 'Worker': 'Write.'},
                                             root, 'http://model/v1', 'test'), 3)
        self.assertIn('Contract file does not exist: contract.md', prompts[1])
        self.assertIn('Use Python to create', prompts[1])
        self.assertNotIn('Handoff file is missing', prompts[1])

    async def test_worker_cannot_execute_without_a_supervisor_contract(self):
        bus = Bus()
        for name in ('Supervisor', 'Worker'):
            bus.register(name)
        agent = Agent('Worker', Intelligence('http://model/v1', 'test'), bus)
        finished = asyncio.get_running_loop().create_future()
        responses = iter([{'python': "from pathlib import Path; Path('unexpected.txt').write_text('work')"},
                          {'to': 'Supervisor', 'text': 'Please provide a contract.'}])
        prompts = []

        async def reply(intelligence, messages, **kwargs):
            prompts.append(messages[-1]['content'])
            return json.dumps(next(responses))

        with tempfile.TemporaryDirectory() as root:
            with patch.object(Intelligence, 'reply', reply), contextlib.redirect_stdout(io.StringIO()):
                actor = asyncio.create_task(work(agent, 'You are Worker.', root, 'Supervisor', finished))
                try:
                    await bus.send('Supervisor', 'Worker', 'A coordination message without a contract.')
                    report = await asyncio.wait_for(bus.receive('Supervisor'), 2)
                    self.assertFalse(Path(root, 'unexpected.txt').exists())
                finally:
                    actor.cancel()
                    await asyncio.gather(actor, return_exceptions=True)
        self.assertEqual(report.text, 'Please provide a contract.')
        self.assertIn('No contract received', prompts[1])

    async def test_worker_cannot_issue_a_contract(self):
        bus = Bus()
        for name in ('Supervisor', 'Worker'):
            bus.register(name)
        agent = Agent('Worker', Intelligence('http://model/v1', 'test'), bus)
        finished = asyncio.get_running_loop().create_future()
        responses = iter([{'to': 'Supervisor', 'text': 'Accept my contract.', 'contract': 'contract.md'},
                          {'to': 'Supervisor', 'text': 'Please issue the contract.'}])
        prompts = []

        async def reply(intelligence, messages, **kwargs):
            prompts.append(messages[-1]['content'])
            return json.dumps(next(responses))

        with tempfile.TemporaryDirectory() as root:
            with patch.object(Intelligence, 'reply', reply), contextlib.redirect_stdout(io.StringIO()):
                actor = asyncio.create_task(work(agent, 'You are Worker.', root, 'Supervisor', finished))
                try:
                    await bus.send('Supervisor', 'Worker', 'Discuss the contract.')
                    report = await asyncio.wait_for(bus.receive('Supervisor'), 2)
                finally:
                    actor.cancel()
                    await asyncio.gather(actor, return_exceptions=True)
        self.assertEqual(report.text, 'Please issue the contract.')
        self.assertIn('Only the Supervisor can issue or update a contract', prompts[1])

    async def test_independent_contracts_run_in_parallel_with_separate_briefs(self):
        started = set()
        ready = asyncio.Event()
        reports = set()
        calls = {}
        assignments = iter([{'to': 'First', 'text': 'Begin.', 'contract': 'first.md', 'wait': False},
                            {'to': 'Second', 'text': 'Begin.', 'contract': 'second.md'}])

        async def reply(intelligence, messages, **kwargs):
            name = messages[0]['content'].splitlines()[0].removeprefix('You are ').removesuffix('.')
            if name == 'Supervisor':
                action = next(assignments, None)
                if action:
                    return json.dumps(action)
                for worker in ('First', 'Second'):
                    if f'{worker}: Ready' in messages[-1]['content']:
                        reports.add(worker)
                return json.dumps({'done': 'Both artifacts received.'} if len(reports) == 2 else {'wait': True})
            calls[name] = calls.get(name, 0) + 1
            if calls[name] == 1:
                self.assertIn(f'Write the word {name}', messages[-1]['content'])
                self.assertNotIn(f'Write the word {"Second" if name == "First" else "First"}', messages[-1]['content'])
                started.add(name)
                if len(started) == 2:
                    ready.set()
                await asyncio.wait_for(ready.wait(), 1)
                return json.dumps({'python': f"from pathlib import Path; Path('{name}.txt').write_text('{name}')"})
            return json.dumps({'to': 'Supervisor', 'text': 'Ready', 'files': [f'{name}.txt']})

        with tempfile.TemporaryDirectory() as root:
            for name in ('First', 'Second'):
                Path(root, f'{name.lower()}.md').write_text(f'# INPUT\n# TASK\nWrite the word {name}.\n# OUTPUT\n- {name}.txt\n')
            with patch.object(Intelligence, 'reply', reply), contextlib.redirect_stdout(io.StringIO()):
                summary = await asyncio.wait_for(start('Produce two independent notes.',
                    dict.fromkeys(('Supervisor', 'First', 'Second'), 'Write your assigned note.'), root, 'http://model/v1', 'test'), 3)
            for name in ('First', 'Second'):
                self.assertEqual(Path(root, f'{name}.txt').read_text(), name)
        self.assertEqual(summary, 'Both artifacts received.')
        self.assertEqual(started, {'First', 'Second'})

    async def test_supervisor_rejects_output_and_sends_an_updated_contract(self):
        draft = '# INPUT\n- source.txt\n# TASK\nWrite a draft in result.txt.\n# OUTPUT\n- result.txt\n'
        correction = '# INPUT\n- source.txt\n- result.txt\n# TASK\nCorrect the draft to the exact word final.\n# OUTPUT\n- result.txt\n'
        scripts = {
            'Supervisor': iter([
                {'python': f"from pathlib import Path; Path('source.txt').write_text('source material'); Path('contract.md').write_text({draft!r})"},
                {'to': 'Writer', 'text': 'Perform the contract.', 'contract': 'contract.md'},
                {'python': f"from pathlib import Path; assert Path('result.txt').read_text() == 'draft'; Path('contract.md').write_text({correction!r})"},
                {'to': 'Writer', 'text': 'Rejected: revise the draft using the updated contract.', 'contract': 'contract.md'},
                {'python': "from pathlib import Path; assert Path('result.txt').read_text() == 'final'; print('Reviewed corrected output')"},
                {'done': 'Accepted the corrected output.'},
            ]),
            'Writer': iter([
                {'python': "from pathlib import Path; print(Path('source.txt').read_text()); Path('result.txt').write_text('draft')"},
                {'to': 'Supervisor', 'text': 'Draft ready for your review.', 'files': ['result.txt']},
                {'python': "from pathlib import Path; assert Path('result.txt').read_text() == 'draft'; Path('result.txt').write_text('final')"},
                {'to': 'Supervisor', 'text': 'Corrected output ready for review.', 'files': ['result.txt']},
            ]),
        }
        inputs = []

        async def reply(intelligence, messages, **kwargs):
            name = messages[0]['content'].splitlines()[0].removeprefix('You are ').removesuffix('.')
            if name == 'Writer':
                inputs.append(messages[-1]['content'])
            return json.dumps(next(scripts[name]))

        with tempfile.TemporaryDirectory() as root:
            with patch.object(Intelligence, 'reply', reply), contextlib.redirect_stdout(io.StringIO()):
                summary = await asyncio.wait_for(start('Produce a short report.', {'Supervisor': 'Review work.', 'Writer': 'Write.'},
                                                       root, 'http://model/v1', 'test'), 3)
            self.assertEqual(Path(root, 'result.txt').read_text(), 'final')
            log = next(Path(root, '.mad/audit').glob('*.jsonl'))
            events = [json.loads(line) for line in log.read_text().splitlines()]
            readable = log.with_suffix('.md').read_text()
        self.assertEqual(summary, 'Accepted the corrected output.')
        self.assertIn('# INPUT\n- source.txt', inputs[0])
        self.assertIn('# TASK\nWrite a draft in result.txt.', inputs[0])
        self.assertIn('# TASK\nCorrect the draft to the exact word final.', inputs[2])
        self.assertIn('# OUTPUT\n- result.txt', inputs[2])
        issued = [e['contract'] for e in events if e['event'] == 'message_sent' and e.get('contract')]
        self.assertEqual(len(issued), 2)
        self.assertEqual([c['OUTPUT'] for c in issued], [['result.txt'], ['result.txt']])
        self.assertIn('Rejected: revise the draft', readable)
        self.assertFalse(any(e['event'] == 'action_failed' for e in events))
