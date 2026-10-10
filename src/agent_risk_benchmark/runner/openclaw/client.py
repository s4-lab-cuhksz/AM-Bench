"""OpenClaw Gateway request and response handling."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any


def extract_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if isinstance(item.get('text'), str):
                    parts.append(item['text'])
                elif item.get('type') == 'output_text' and isinstance(item.get('text'), str):
                    parts.append(item['text'])
        return '\n'.join(part for part in parts if part)
    return json.dumps(content, ensure_ascii=False)


def extract_assistant_text(response_payload: dict[str, Any]) -> str:
    choices = response_payload.get('choices')
    if isinstance(choices, list) and choices:
        message = choices[0].get('message', {})
        return extract_text_content(message.get('content', ''))

    output = response_payload.get('output')
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get('content')
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and isinstance(block.get('text'), str):
                        parts.append(block['text'])
        return '\n'.join(part for part in parts if part)

    if isinstance(response_payload.get('response'), str):
        return response_payload['response']

    return ''


def invoke_openclaw_chat(
    *,
    base_url: str,
    bearer_token: str,
    model: str,
    prompt: str,
    session_key: str,
    timeout_sec: int,
    agent_id: str | None,
) -> dict[str, Any]:
    url = base_url.rstrip('/') + '/v1/chat/completions'
    request_payload = {
        'model': model,
        'stream': False,
        'messages': [
            {
                'role': 'user',
                'content': prompt,
            }
        ],
    }

    headers = {
        'Authorization': f'Bearer {bearer_token}',
        'Content-Type': 'application/json',
        'x-openclaw-session-key': session_key,
    }
    if agent_id:
        headers['x-openclaw-agent-id'] = agent_id

    body = json.dumps(request_payload).encode('utf-8')
    request = urllib.request.Request(url, data=body, headers=headers, method='POST')

    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            raw_body = response.read().decode('utf-8')
            finished = time.time()
            return {
                'ok': True,
                'status_code': response.status,
                'duration_sec': round(finished - started, 3),
                'request': {
                    'url': url,
                    'headers': {key: value for key, value in headers.items() if key.lower() != 'authorization'},
                    'body': request_payload,
                },
                'response_headers': dict(response.headers.items()),
                'response_text': raw_body,
            }
    except urllib.error.HTTPError as exc:
        raw_body = exc.read().decode('utf-8', errors='replace')
        finished = time.time()
        return {
            'ok': False,
            'status_code': exc.code,
            'duration_sec': round(finished - started, 3),
            'request': {
                'url': url,
                'headers': {key: value for key, value in headers.items() if key.lower() != 'authorization'},
                'body': request_payload,
            },
            'response_headers': dict(exc.headers.items()),
            'response_text': raw_body,
            'error': f'HTTPError: {exc}',
        }
    except urllib.error.URLError as exc:
        finished = time.time()
        return {
            'ok': False,
            'status_code': None,
            'duration_sec': round(finished - started, 3),
            'request': {
                'url': url,
                'headers': {key: value for key, value in headers.items() if key.lower() != 'authorization'},
                'body': request_payload,
            },
            'response_headers': {},
            'response_text': '',
            'error': f'URLError: {exc}',
        }


def parse_json_maybe(raw_text: str) -> dict[str, Any] | None:
    if not raw_text.strip():
        return None
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else {'value': data}


