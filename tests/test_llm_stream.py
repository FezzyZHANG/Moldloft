"""Provider streaming contract, using synthetic SSE and no model calls."""
import io
import json
import sys
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from tickets import llm


def frame(delta=None, finish=None):
    data = {'choices': [{'index': 0, 'delta': delta or {}, 'finish_reason': finish}]}
    return ('data: ' + json.dumps(data, ensure_ascii=False) + '\n\n').encode('utf-8')


def tool(arguments, name='propose_ticket_changes'):
    return {'tool_calls': [{'index': 0, 'id': 'fixture', 'function': {'name': name, 'arguments': arguments}}]}


class StreamTests(unittest.TestCase):
    def test_split_tools_and_utf8_text_exclude_reasoning(self):
        response = io.BytesIO(frame({'reasoning_content': 'private reasoning', 'content': '预览'})
                              + frame(tool('{"operations":')) + frame(tool('[]}', name=''))
                              + frame(finish='tool_calls') + b'data: [DONE]\n\n')
        result = list(llm._read_stream(response))
        self.assertEqual(result, [{'type': 'text', 'text': '预览'},
                                  {'type': 'tool', 'name': 'propose_ticket_changes', 'arguments': {'operations': []}, 'id': 'fixture'},
                                  {'type': 'done'}])

    def test_incomplete_length_and_malformed_calls_never_yield_tool(self):
        cases = [frame(tool('{}')), frame(tool('{}')) + frame(finish='length') + b'data: [DONE]\n\n',
                 frame(tool('{')) + frame(finish='tool_calls') + b'data: [DONE]\n\n',
                 b'data: not-json\n\n']
        for raw in cases:
            seen = []
            with self.subTest(raw=raw), self.assertRaises(llm.LLMError):
                for event in llm._read_stream(io.BytesIO(raw)):
                    seen.append(event)
            self.assertFalse(any(e['type'] in ('tool', 'done') for e in seen))

    def test_cancel_discards_tool_and_request_payload_stays_role_driven(self):
        cancelled = threading.Event(); cancelled.set()
        with self.assertRaises(llm.LLMError):
            list(llm._read_stream(io.BytesIO(frame(tool('{}'))), cancelled))
        captured = []
        def opening(request, timeout):
            captured.append(json.loads(request.data))
            self.assertEqual(timeout, 60)
            return io.BytesIO(frame({'content': 'Done'}, 'stop') + b'data: [DONE]\n\n')
        with patch.dict(llm.os.environ, {'TICKETS_COPILOT': 'on', 'ZAI_API_KEY': 'fixture-only', 'TICKETS_COPILOT_MODEL': 'glm-5.3'}), \
                patch.object(llm.urllib.request, 'urlopen', opening):
            list(llm.stream('tickets_copilot', messages=[{'role': 'user', 'content': 'Synthetic'}],
                            system='Fixture', tools=[{'type': 'function'}]))
        self.assertEqual(captured[0]['model'], 'glm-5.3')
        self.assertTrue(captured[0]['tool_stream'])
        self.assertEqual(captured[0]['reasoning_effort'], 'low')

    def test_http_error_does_not_expose_provider_body(self):
        error = urllib.error.HTTPError('https://example.invalid', 429, 'limit', {}, io.BytesIO(b'synthetic-secret'))
        with patch.dict(llm.os.environ, {'TICKETS_COPILOT': 'on', 'ZAI_API_KEY': 'fixture-only'}), \
                patch.object(llm.urllib.request, 'urlopen', side_effect=error):
            with self.assertRaisesRegex(llm.LLMError, r'HTTP 429') as result:
                list(llm.stream('tickets_copilot', messages=[], system='Fixture'))
        self.assertNotIn('synthetic-secret', str(result.exception))


if __name__ == '__main__':
    unittest.main()
