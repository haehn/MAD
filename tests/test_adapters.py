import io
import unittest
from unittest.mock import Mock, patch

from rich.console import Console
from mad.llm import LLM
from mad.project import Project
from mad.reporter import Reporter


class AdapterTests(unittest.TestCase):
    @patch('mad.llm.requests.post')
    def test_model_request_preserves_endpoint_and_schema(self, post):
        post.return_value = Mock(ok=True)
        post.return_value.json.return_value = {
            'choices': [{'message': {'content': '<think>internal</think> {"ok":true}'}}],
        }
        schema = {'type': 'object'}
        result = LLM('http://model/v1/').chat('test-model', [], json_schema=schema)
        self.assertEqual(result, '{"ok":true}')
        args, kwargs = post.call_args
        self.assertEqual(args[0], 'http://model/v1/chat/completions')
        self.assertEqual(kwargs['json']['response_format']['json_schema']['schema'], schema)
        self.assertEqual(kwargs['json']['model'], 'test-model')
        self.assertEqual(kwargs['timeout'], 300)

    @patch('mad.llm.requests.post')
    def test_model_error_is_propagated(self, post):
        post.return_value = Mock(ok=False, status_code=503)
        post.return_value.json.return_value = {'error': 'unavailable'}
        with self.assertRaisesRegex(RuntimeError, '503'):
            LLM().chat('test-model', [])

    @patch('mad.llm.requests.post')
    def test_show_thinking_remains_available(self, post):
        post.return_value = Mock(ok=True)
        post.return_value.json.return_value = {
            'choices': [{'message': {'content': '<think>reasoning</think> answer'}}],
        }
        self.assertIn('<think>', LLM(show_thinking=True).chat('test', []))

    def test_all_presentation_uses_injected_output(self):
        output = io.StringIO()
        reporter = Reporter(Console(file=output))
        reporter.start(Project('Example objective', '.', agents=[]), '.', verbose=True)
        self.assertIn('MULTI AGENT DRIVER', output.getvalue())
        self.assertIn('Example objective', output.getvalue())
