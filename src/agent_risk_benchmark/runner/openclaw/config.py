"""OpenClaw configuration discovery and workspace synchronization."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from ..constants import DEFAULT_AGENT_ID, DEFAULT_BASE_URL, DEFAULT_WORKSPACE_APPLY_WAIT_SEC


def load_json_dict(path: Path, *, label: str) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(raw, dict):
        raise ValueError(f'{label} must contain a JSON object: {path}')
    return raw


def derive_openclaw_host(bind_value: Any) -> str:
    if not isinstance(bind_value, str) or not bind_value.strip():
        return '127.0.0.1'
    bind_value = bind_value.strip()
    if bind_value in {'loopback', 'localhost', '127.0.0.1', '0.0.0.0'}:
        return '127.0.0.1'
    return bind_value


def derive_base_url_from_openclaw_config(openclaw_config: dict[str, Any]) -> str | None:
    gateway = openclaw_config.get('gateway')
    if not isinstance(gateway, dict):
        return None
    port = gateway.get('port')
    if not isinstance(port, int):
        return None
    host = derive_openclaw_host(gateway.get('bind'))
    return f'http://{host}:{port}'


def extract_gateway_token(openclaw_config: dict[str, Any]) -> str | None:
    gateway = openclaw_config.get('gateway')
    if not isinstance(gateway, dict):
        return None
    auth = gateway.get('auth')
    if not isinstance(auth, dict):
        return None
    token = auth.get('token')
    return token.strip() if isinstance(token, str) and token.strip() else None


def resolve_openclaw_settings(args: argparse.Namespace) -> dict[str, Any]:
    openclaw_home_raw = (
        getattr(args, 'openclaw_home', None)
        or os.environ.get('OPENCLAW_HOME')
    )
    openclaw_config_raw = getattr(args, 'openclaw_config', None) or os.environ.get('OPENCLAW_CONFIG')
    openclaw_home: Path | None = None
    openclaw_config_path: Path | None = None
    openclaw_config: dict[str, Any] | None = None
    sessions_index_path: Path | None = None

    if isinstance(openclaw_config_raw, str) and openclaw_config_raw.strip():
        openclaw_config_path = Path(openclaw_config_raw).expanduser().resolve()
        if not openclaw_config_path.exists():
            raise FileNotFoundError(f'missing OpenClaw config: {openclaw_config_path}')
        openclaw_config = load_json_dict(openclaw_config_path, label='OpenClaw config')
        openclaw_home = openclaw_config_path.parent
    elif isinstance(openclaw_home_raw, str) and openclaw_home_raw.strip():
        openclaw_home = Path(openclaw_home_raw).expanduser().resolve()
        openclaw_config_path = openclaw_home / 'openclaw.json'
        if not openclaw_config_path.exists():
            raise FileNotFoundError(f'missing OpenClaw config: {openclaw_config_path}')
        openclaw_config = load_json_dict(openclaw_config_path, label='OpenClaw config')

    agent_id = (
        getattr(args, 'agent_id', None)
        or os.environ.get('OPENCLAW_AGENT_ID')
        or DEFAULT_AGENT_ID
    )
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise ValueError('agent_id must be a non-empty string')
    agent_id = agent_id.strip()

    if openclaw_home is not None:
        sessions_index_path = openclaw_home / 'agents' / agent_id / 'sessions' / 'sessions.json'

    base_url = getattr(args, 'base_url', None) or os.environ.get('OPENCLAW_BASE_URL')
    if not base_url and openclaw_config is not None:
        base_url = derive_base_url_from_openclaw_config(openclaw_config)
    if not base_url:
        base_url = DEFAULT_BASE_URL

    bearer_token = (
        getattr(args, 'bearer_token', None)
        or os.environ.get('OPENCLAW_BEARER_TOKEN')
        or os.environ.get('OPENCLAW_BENCH_TOKEN')
    )
    if not bearer_token and openclaw_config is not None:
        bearer_token = extract_gateway_token(openclaw_config)

    model = getattr(args, 'model', None) or f'openclaw:{agent_id}'

    return {
        'openclaw_home': openclaw_home,
        'openclaw_config_path': openclaw_config_path,
        'openclaw_config': openclaw_config,
        'sessions_index_path': sessions_index_path,
        'base_url': base_url,
        'bearer_token': bearer_token,
        'model': model,
        'agent_id': agent_id,
    }


def sync_openclaw_workspace(openclaw_config_path: Path | None, workspace_dir: Path) -> str | None:
    if openclaw_config_path is None:
        return None

    config = load_json_dict(openclaw_config_path, label='OpenClaw config')
    agents = config.setdefault('agents', {})
    if not isinstance(agents, dict):
        raise ValueError(f'OpenClaw config has invalid agents section: {openclaw_config_path}')
    defaults = agents.setdefault('defaults', {})
    if not isinstance(defaults, dict):
        raise ValueError(f'OpenClaw config has invalid agents.defaults section: {openclaw_config_path}')
    defaults['workspace'] = str(workspace_dir)
    openclaw_config_path.write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
    return str(openclaw_config_path)


def wait_for_openclaw_workspace_apply(openclaw_config_path: Path | None, wait_sec: float = DEFAULT_WORKSPACE_APPLY_WAIT_SEC) -> float:
    if openclaw_config_path is None:
        return 0.0
    if wait_sec <= 0:
        return 0.0
    time.sleep(wait_sec)
    return wait_sec


