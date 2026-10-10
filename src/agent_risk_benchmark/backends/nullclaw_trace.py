"""Normalize NullClaw diagnostic logs to the benchmark trace schema."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .base import empty_token_usage
from .trace import _write_jsonl

_RESPONSE_RE = re.compile(
    r'info\(agent\): llm response .*? iter=(?P<iteration>\d+) '
    r'attempt=(?P<attempt>\d+) .*? '
    r'usage=(?P<usage>\{.*?\}) content=(?P<content>"(?:\\.|[^"\\])*")'
)
_NATIVE_TOOL_CALL_RE = re.compile(
    r'info\(agent\): llm response tool-call .*? iter=(?P<iteration>\d+) '
    r'attempt=(?P<attempt>\d+) index=(?P<index>\d+) id=(?P<id>\S+) '
    r'name=(?P<name>\S+) args=(?P<arguments>"(?:\\.|[^"\\])*")'
)
_REQUEST_MESSAGE_RE = re.compile(
    r'info\(agent\): llm request msg .*? iter=(?P<iteration>\d+) .*? '
    r'index=(?P<index>\d+) role=(?P<role>\w+) .*? content=(?P<content>"(?:\\.|[^"\\])*")'
)
_TOOL_CALL_RE = re.compile(r'<tool_call>\s*(\{.*?\})\s*</tool_call>', re.DOTALL)
_TOOL_RESULT_RE = re.compile(
    r'<tool_result\s+name="(?P<name>[^"]+)"\s+status="(?P<status>[^"]+)">\s*'
    r'(?P<output>.*?)\s*</tool_result>',
    re.DOTALL,
)


def _decode_json_string(raw: str) -> str:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return ''
    return value if isinstance(value, str) else ''


def _tool_calls(content: str) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for match in _TOOL_CALL_RE.finditer(content):
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        arguments = payload.get('arguments')
        if not isinstance(arguments, dict):
            arguments = {}
        calls.append({'id': '', 'name': str(payload.get('name') or ''), 'arguments': arguments})
    return calls


def _native_tool_calls(raw: str) -> dict[tuple[int, int], list[dict[str, Any]]]:
    calls_by_response: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for match in _NATIVE_TOOL_CALL_RE.finditer(raw):
        arguments_text = _decode_json_string(match.group('arguments'))
        try:
            arguments = json.loads(arguments_text)
        except json.JSONDecodeError:
            arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        key = (int(match.group('iteration')), int(match.group('attempt')))
        calls_by_response.setdefault(key, []).append({
            'id': match.group('id'),
            'name': match.group('name'),
            'arguments': arguments,
        })
    return calls_by_response


def normalize_nullclaw_trace(raw_path: Path, output_path: Path) -> dict[str, int]:
    """Convert NullClaw 2026.5.29 LLM diagnostics into canonical JSONL."""
    raw = raw_path.read_text(encoding='utf-8', errors='replace') if raw_path.exists() else ''
    canonical: list[dict[str, Any]] = []
    usage = empty_token_usage()
    pending: list[tuple[str, str]] = []
    call_index = 0
    native_calls_by_response = _native_tool_calls(raw)

    events: list[tuple[int, int, str, re.Match[str]]] = []
    for match in _RESPONSE_RE.finditer(raw):
        events.append((match.start(), int(match.group('iteration')), 'response', match))
    latest_tool_results: dict[int, tuple[int, re.Match[str]]] = {}
    for match in _REQUEST_MESSAGE_RE.finditer(raw):
        content = _decode_json_string(match.group('content'))
        if content.startswith('[Tool results]'):
            iteration = int(match.group('iteration'))
            index = int(match.group('index'))
            current = latest_tool_results.get(iteration)
            if current is None or index > current[0]:
                latest_tool_results[iteration] = (index, match)
    for iteration, (_, match) in latest_tool_results.items():
        events.append((match.start(), iteration, 'tool_results', match))
    events.sort(key=lambda item: item[0])

    for _, iteration, event_type, match in events:
        content = _decode_json_string(match.group('content'))
        if event_type == 'response':
            try:
                raw_usage = json.loads(match.group('usage'))
            except json.JSONDecodeError:
                raw_usage = {}
            if isinstance(raw_usage, dict):
                prompt_tokens = int(raw_usage.get('prompt_tokens', 0) or 0)
                completion_tokens = int(raw_usage.get('completion_tokens', 0) or 0)
                total_tokens = int(
                    raw_usage.get('total_tokens', prompt_tokens + completion_tokens) or 0
                )
                usage['input'] += prompt_tokens
                usage['output'] += max(completion_tokens, total_tokens - prompt_tokens)
                usage['cache_read'] += int(
                    raw_usage.get(
                        'prompt_cache_hit_tokens',
                        raw_usage.get('cached_tokens', 0),
                    )
                    or 0
                )

            response_key = (iteration, int(match.group('attempt')))
            calls = native_calls_by_response.get(response_key) or _tool_calls(content)
            if calls:
                blocks: list[dict[str, Any]] = []
                for call in calls:
                    call_index += 1
                    logged_call_id = str(call.get('id') or '')
                    call_id = (
                        logged_call_id
                        if logged_call_id != '-'
                        else f'nullclaw-{iteration}-{call_index}'
                    )
                    name = str(call['name'])
                    pending.append((call_id, name))
                    blocks.append({
                        'type': 'toolCall',
                        'id': call_id,
                        'name': name,
                        'arguments': call['arguments'],
                    })
                canonical.append({'type': 'message', 'message': {'role': 'assistant', 'content': blocks}})
            elif content.strip():
                canonical.append({
                    'type': 'message',
                    'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': content.strip()}]},
                })
            continue

        for result in _TOOL_RESULT_RE.finditer(content):
            logged_name = result.group('name')
            if pending:
                call_id, call_name = pending.pop(0)
            else:
                call_index += 1
                call_id, call_name = f'nullclaw-result-{call_index}', logged_name
            status = result.group('status').strip().lower()
            success = status in {'ok', 'success', 'completed'}
            canonical.append({
                'type': 'message',
                'message': {
                    'role': 'toolResult',
                    'toolCallId': call_id,
                    'toolName': call_name or logged_name,
                    'isError': not success,
                    'content': [{'type': 'text', 'text': result.group('output').strip()}],
                    'details': {'status': 'completed' if success else status},
                },
            })

    usage['total'] = usage['input'] + usage['output']
    if usage['total']:
        canonical.append({'type': 'message', 'message': {'role': 'assistant', 'content': [], 'usage': usage}})
    _write_jsonl(output_path, canonical)
    return usage
