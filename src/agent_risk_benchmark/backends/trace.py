"""Trace conversion helpers for non-OpenClaw harnesses."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import empty_token_usage


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text(
        ''.join(json.dumps(record, ensure_ascii=False) + '\n' for record in records),
        encoding='utf-8',
    )


def _read_json_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not path.exists():
        return events
    for raw_line in path.read_text(encoding='utf-8', errors='ignore').splitlines():
        line = raw_line.strip()
        if not line.startswith('{'):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def normalize_codex_trace(raw_path: Path, output_path: Path) -> dict[str, int]:
    """Convert ``codex exec --json`` events to the OpenClaw-compatible trace used by oracles."""
    canonical: list[dict[str, Any]] = []
    usage = empty_token_usage()
    call_index = 0

    for event in _read_json_events(raw_path):
        event_type = str(event.get('type', ''))
        if event_type == 'turn.completed':
            raw_usage = event.get('usage')
            if isinstance(raw_usage, dict):
                usage['input'] += int(raw_usage.get('input_tokens', 0) or 0)
                usage['output'] += int(raw_usage.get('output_tokens', 0) or 0)
                usage['cache_read'] += int(raw_usage.get('cached_input_tokens', 0) or 0)
                usage['cache_write'] += int(raw_usage.get('cache_write_tokens', 0) or 0)
            continue

        if event_type not in {'item.started', 'item.completed'}:
            continue
        item = event.get('item')
        if not isinstance(item, dict):
            continue
        item_type = str(item.get('type', ''))

        if item_type == 'agent_message' and event_type == 'item.completed':
            text = str(item.get('text', '')).strip()
            if text:
                canonical.append(
                    {'type': 'message', 'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': text}]}}
                )
            continue

        if item_type != 'command_execution' or event_type != 'item.completed':
            continue
        call_index += 1
        call_id = str(item.get('id') or f'codex-command-{call_index}')
        command = item.get('command', '')
        if isinstance(command, list):
            command = ' '.join(str(part) for part in command)
        command = str(command)
        canonical.append(
            {
                'type': 'message',
                'message': {
                    'role': 'assistant',
                    'content': [
                        {
                            'type': 'toolCall',
                            'id': call_id,
                            'name': 'exec',
                            'arguments': {'command': command},
                        }
                    ],
                },
            }
        )
        exit_code = item.get('exit_code')
        status = str(item.get('status', ''))
        result_text = str(item.get('aggregated_output', item.get('output', '')))
        canonical.append(
            {
                'type': 'message',
                'message': {
                    'role': 'toolResult',
                    'toolCallId': call_id,
                    'toolName': 'exec',
                    'isError': bool(status == 'failed' or (isinstance(exit_code, int) and exit_code != 0)),
                    'content': [{'type': 'text', 'text': result_text}],
                    'details': {'exitCode': exit_code},
                },
            }
        )

    # Codex reports cached input as a subset of input_tokens, so do not double-count it.
    usage['total'] = usage['input'] + usage['output']
    if usage['total']:
        canonical.append(
            {
                'type': 'message',
                'message': {'role': 'assistant', 'content': [], 'usage': usage},
            }
        )
    _write_jsonl(output_path, canonical)
    return usage

