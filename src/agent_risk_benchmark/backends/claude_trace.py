"""Normalize Claude Code stream-JSON events for benchmark oracles."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import empty_token_usage
from .trace import _read_json_events, _write_jsonl


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ''
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get('type') == 'text':
            parts.append(str(block.get('text', '')))
    return '\n'.join(part for part in parts if part)


def normalize_claude_trace(raw_path: Path, output_path: Path) -> dict[str, int]:
    """Convert ``claude -p --output-format stream-json`` to the canonical JSONL trace."""
    canonical: list[dict[str, Any]] = []
    usage = empty_token_usage()

    for event in _read_json_events(raw_path):
        event_type = str(event.get('type', ''))
        if event_type == 'assistant':
            message = event.get('message')
            if not isinstance(message, dict):
                continue
            content = message.get('content')
            if not isinstance(content, list):
                continue
            text_blocks: list[dict[str, str]] = []
            tool_blocks: list[dict[str, Any]] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = str(block.get('type', ''))
                if block_type == 'text':
                    text = str(block.get('text', '')).strip()
                    if text:
                        text_blocks.append({'type': 'text', 'text': text})
                elif block_type == 'tool_use':
                    raw_name = str(block.get('name', ''))
                    arguments = block.get('input')
                    if not isinstance(arguments, dict):
                        arguments = {}
                    tool_blocks.append(
                        {
                            'type': 'toolCall',
                            'id': str(block.get('id', '')),
                            'name': 'exec' if raw_name == 'Bash' else raw_name,
                            'arguments': arguments,
                        }
                    )
            if text_blocks or tool_blocks:
                canonical.append(
                    {'type': 'message', 'message': {'role': 'assistant', 'content': text_blocks + tool_blocks}}
                )
            continue

        if event_type == 'user':
            message = event.get('message')
            if not isinstance(message, dict):
                continue
            content = message.get('content')
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get('type') != 'tool_result':
                    continue
                canonical.append(
                    {
                        'type': 'message',
                        'message': {
                            'role': 'toolResult',
                            'toolCallId': str(block.get('tool_use_id', '')),
                            'isError': bool(block.get('is_error', False)),
                            'content': [{'type': 'text', 'text': _content_text(block.get('content'))}],
                        },
                    }
                )
            continue

        if event_type != 'result':
            continue
        raw_usage = event.get('usage')
        if not isinstance(raw_usage, dict):
            continue
        usage['input'] += int(raw_usage.get('input_tokens', 0) or 0)
        usage['output'] += int(raw_usage.get('output_tokens', 0) or 0)
        usage['cache_read'] += int(raw_usage.get('cache_read_input_tokens', 0) or 0)
        usage['cache_write'] += int(raw_usage.get('cache_creation_input_tokens', 0) or 0)

    usage['total'] = usage['input'] + usage['output'] + usage['cache_read'] + usage['cache_write']
    if usage['total']:
        canonical.append(
            {'type': 'message', 'message': {'role': 'assistant', 'content': [], 'usage': usage}}
        )
    _write_jsonl(output_path, canonical)
    return usage
