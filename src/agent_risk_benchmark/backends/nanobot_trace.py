"""Normalize nanobot SDK results to the canonical benchmark trace schema."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import empty_token_usage
from .trace import _write_jsonl


def _text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ''
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and isinstance(block.get('text'), str):
            parts.append(block['text'])
    return '\n'.join(part for part in parts if part)


def _tool_arguments(tool_call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    function = tool_call.get('function')
    if not isinstance(function, dict):
        function = tool_call
    name = str(function.get('name') or tool_call.get('name') or '')
    raw_arguments = function.get('arguments', tool_call.get('arguments', {}))
    if isinstance(raw_arguments, dict):
        return name, raw_arguments
    if isinstance(raw_arguments, str) and raw_arguments.strip():
        try:
            parsed = json.loads(raw_arguments)
        except json.JSONDecodeError:
            return name, {'raw': raw_arguments}
        if isinstance(parsed, dict):
            return name, parsed
    return name, {}


def normalize_nanobot_trace(raw_path: Path, output_path: Path) -> dict[str, int]:
    payload = json.loads(raw_path.read_text(encoding='utf-8'))
    messages = payload.get('messages')
    if not isinstance(messages, list):
        messages = []
    canonical: list[dict[str, Any]] = []

    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get('role', ''))
        timestamp = message.get('timestamp')
        event: dict[str, Any] | None = None
        if role == 'assistant':
            blocks: list[dict[str, Any]] = []
            text = _text_content(message.get('content')).strip()
            if text:
                blocks.append({'type': 'text', 'text': text})
            tool_calls = message.get('tool_calls')
            if isinstance(tool_calls, list):
                for index, tool_call in enumerate(tool_calls, start=1):
                    if not isinstance(tool_call, dict):
                        continue
                    name, arguments = _tool_arguments(tool_call)
                    blocks.append(
                        {
                            'type': 'toolCall',
                            'id': str(tool_call.get('id') or f'nanobot-tool-{index}'),
                            'name': name,
                            'arguments': arguments,
                        }
                    )
            if blocks:
                event = {'type': 'message', 'message': {'role': 'assistant', 'content': blocks}}
        elif role == 'tool':
            event = {
                'type': 'message',
                'message': {
                    'role': 'toolResult',
                    'toolCallId': str(message.get('tool_call_id', '')),
                    'toolName': str(message.get('name', '')),
                    'isError': bool(message.get('is_error', False)),
                    'content': [{'type': 'text', 'text': _text_content(message.get('content'))}],
                },
            }
        elif role == 'user':
            text = _text_content(message.get('content')).strip()
            if text:
                event = {
                    'type': 'message',
                    'message': {'role': 'user', 'content': [{'type': 'text', 'text': text}]},
                }
        if event is not None:
            if timestamp is not None:
                event['timestamp'] = timestamp
            canonical.append(event)

    raw_usage = payload.get('usage')
    usage = empty_token_usage()
    if isinstance(raw_usage, dict):
        usage['input'] = int(raw_usage.get('prompt_tokens', raw_usage.get('input', 0)) or 0)
        usage['output'] = int(raw_usage.get('completion_tokens', raw_usage.get('output', 0)) or 0)
        usage['cache_read'] = int(raw_usage.get('cached_tokens', raw_usage.get('cache_read', 0)) or 0)
        usage['cache_write'] = int(raw_usage.get('cache_write', 0) or 0)
        usage['total'] = int(
            raw_usage.get('total_tokens', raw_usage.get('total', usage['input'] + usage['output'])) or 0
        )
    if usage['total']:
        canonical.append({'type': 'message', 'message': {'role': 'assistant', 'content': [], 'usage': usage}})

    _write_jsonl(output_path, canonical)
    return usage
