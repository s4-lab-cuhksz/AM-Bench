from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_risk_benchmark.backends.claude_trace import normalize_claude_trace


class ClaudeTraceTests(unittest.TestCase):
    def test_tool_calls_results_messages_and_usage_are_normalized(self) -> None:
        events = [
            {
                'type': 'assistant',
                'message': {
                    'content': [
                        {
                            'type': 'tool_use',
                            'id': 'tool-1',
                            'name': 'Bash',
                            'input': {'command': 'python3 tools/list_sources.py'},
                        }
                    ]
                },
            },
            {
                'type': 'user',
                'message': {
                    'content': [
                        {
                            'type': 'tool_result',
                            'tool_use_id': 'tool-1',
                            'content': 'official_media: 4',
                            'is_error': False,
                        }
                    ]
                },
            },
            {
                'type': 'assistant',
                'message': {'content': [{'type': 'text', 'text': 'Finished.'}]},
            },
            {
                'type': 'result',
                'usage': {
                    'input_tokens': 100,
                    'output_tokens': 20,
                    'cache_read_input_tokens': 60,
                    'cache_creation_input_tokens': 10,
                },
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / 'claude-events.log'
            output = root / 'bench-run.jsonl'
            raw.write_text(''.join(json.dumps(event) + '\n' for event in events), encoding='utf-8')

            usage = normalize_claude_trace(raw, output)
            records = [json.loads(line) for line in output.read_text(encoding='utf-8').splitlines()]

        self.assertEqual(
            usage,
            {'input': 100, 'output': 20, 'cache_read': 60, 'cache_write': 10, 'total': 190},
        )
        tool_call = records[0]['message']['content'][0]
        self.assertEqual(tool_call['name'], 'exec')
        self.assertEqual(tool_call['arguments']['command'], 'python3 tools/list_sources.py')
        self.assertEqual(records[1]['message']['role'], 'toolResult')
        self.assertEqual(records[1]['message']['toolCallId'], 'tool-1')
        self.assertEqual(records[2]['message']['content'][0]['text'], 'Finished.')


if __name__ == '__main__':
    unittest.main()
