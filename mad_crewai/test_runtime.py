import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mad_crew.__main__ import project
from .runtime import Runtime


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.runtime = Runtime('Write a greeting.', self.directory.name,
                               'http://localhost:8000/v1', 'local-model', workers=2)

    def test_execution_uses_workspace_and_records_actual_failure(self):
        result = json.loads(self.runtime.execute("from pathlib import Path; Path('note.txt').write_text('hello'); print('actual output'); raise ValueError('actual failure')"))
        self.assertEqual((Path(self.directory.name) / 'note.txt').read_text(), 'hello')
        self.assertEqual(result['returncode'], 1)
        self.assertIn('actual output', result['stdout'])
        self.assertIn('actual failure', result['stderr'])
        events = [json.loads(line) for line in (self.runtime.audit / 'events.jsonl').read_text().splitlines()]
        self.assertEqual([event['event'] for event in events], ['execution_started', 'execution_finished'])
        readable = (self.runtime.audit / 'events.md').read_text()
        self.assertIn('actual output', readable)
        self.assertIn('actual failure', readable)
        self.assertFalse(result['timed_out'])

    def test_timeout_is_recorded_and_process_reaped(self):
        import subprocess
        process = unittest.mock.Mock(pid=123, returncode=-9)
        process.communicate.side_effect = [subprocess.TimeoutExpired('python', 30), ('partial', 'timeout')]
        with patch('mad_crewai.runtime.subprocess.Popen', return_value=process), patch('mad_crewai.runtime.os.killpg') as kill:
            result = json.loads(self.runtime.execute('while True: pass'))
        kill.assert_called_once()
        self.assertTrue(result['timed_out'])
        self.assertEqual(result['returncode'], -9)
        self.assertEqual(process.communicate.call_count, 2)

    def test_example_has_separate_workspace_and_preserves_objective(self):
        task, workspace = project(Path(__file__).parent / 'examples/solar.md')
        self.assertEqual(workspace, './crewai-solar')
        self.assertIn('Earth orbits the Sun', task)
        self.assertIn('Success conditions:', task)

    def test_invalid_worker_count(self):
        for workers in (0, -1, True):
            with self.subTest(workers=workers), self.assertRaises(ValueError):
                Runtime('task', self.directory.name, 'server', 'model', workers)

    @unittest.skipUnless(importlib.util.find_spec('crewai'), 'CrewAI is an optional isolated dependency')
    def test_real_crewai_builds_hierarchy_with_worker_only_execution(self):
        from crewai import Process
        crew = self.runtime.crew()
        self.assertEqual(crew.process, Process.hierarchical)
        self.assertEqual(crew.manager_agent.role, 'Supervisor')
        self.assertTrue(crew.manager_agent.allow_delegation)
        self.assertEqual(crew.manager_agent.tools, [])
        self.assertEqual([agent.role for agent in crew.agents], ['Worker-1', 'Worker-2'])
        for agent in crew.agents:
            self.assertFalse(agent.allow_delegation)
            self.assertEqual([tool.name for tool in agent.tools], ['workspace_python'])
        self.assertEqual(crew.tasks[0].description, 'Write a greeting.')
        self.assertEqual(crew.manager_agent.llm.base_url, 'http://localhost:8000/v1')
        self.assertFalse(crew.cache)
        self.assertFalse(crew.memory)

    @unittest.skipUnless(importlib.util.find_spec('crewai'), 'CrewAI is an optional isolated dependency')
    def test_crewai_delegates_and_worker_executes_through_native_tools(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Thread
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                calls.append(request)
                names = [item['function']['name'] for item in request.get('tools', [])]
                worker = 'workspace_python' in names
                if any(message['role'] == 'tool' for message in request['messages']):
                    message = {'role': 'assistant', 'content': 'Worker verified greeting.txt.' if worker else 'Accepted: greeting.txt was written and verified.'}
                    finish = 'stop'
                else:
                    name = 'workspace_python' if worker else next(name for name in names if 'delegate' in name.lower())
                    arguments = {'code': "from pathlib import Path; Path('greeting.txt').write_text('hello'); assert Path('greeting.txt').read_text() == 'hello'; print('verified hello')"} if worker else {'coworker': 'Worker-1', 'task': 'Write greeting.txt containing hello and verify it.', 'context': 'Use the workspace and report actual results.'}
                    message = {'role': 'assistant', 'content': None, 'tool_calls': [{'id': f'call_{len(calls)}', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments)}}]}
                    finish = 'tool_calls'
                response = json.dumps({'id': 'test', 'object': 'chat.completion', 'created': 0,
                                       'model': 'local-model', 'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
                                       'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(response)))
                self.end_headers()
                self.wfile.write(response)

        with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                self.runtime.server = f'http://127.0.0.1:{server.server_port}/v1'
                result = self.runtime.run()
            finally:
                server.shutdown()
                thread.join()
        self.assertIn('Accepted', result)
        self.assertEqual((self.runtime.workspace / 'greeting.txt').read_text(), 'hello')
        self.assertEqual(len(calls), 4)
        events = [json.loads(line) for line in (self.runtime.audit / 'events.jsonl').read_text().splitlines()]
        execution = next(event for event in events if event['event'] == 'execution_finished')
        self.assertEqual(execution['returncode'], 0)
        self.assertIn('verified hello', execution['stdout'])
