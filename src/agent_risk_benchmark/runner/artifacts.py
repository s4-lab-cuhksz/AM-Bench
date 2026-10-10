"""Run directory materialization and artifact persistence."""

from __future__ import annotations

import json
import secrets
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .constants import DEFAULT_SUITE
from .repository import RUNS_ROOT
from .tools import load_case_config


def load_run_metadata(run_dir: Path) -> dict[str, Any]:
    metadata_path = run_dir / 'metadata.json'
    if not metadata_path.exists():
        raise FileNotFoundError(f'missing run metadata: {metadata_path}')
    return json.loads(metadata_path.read_text(encoding='utf-8'))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')


def update_metadata(run_dir: Path, **updates: Any) -> dict[str, Any]:
    metadata = load_run_metadata(run_dir)
    metadata.update(updates)
    write_json(run_dir / 'metadata.json', metadata)
    return metadata


def next_run_name(case_root: Path) -> str:
    max_index = 0
    for child in case_root.iterdir() if case_root.exists() else []:
        if child.is_dir() and child.name.startswith('run'):
            suffix = child.name[3:]
            if suffix.isdigit():
                max_index = max(max_index, int(suffix))
    return f'run{max_index + 1}'


def build_run_id(run_date: str, run_name: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    suffix = secrets.token_hex(4)
    return f'{run_date}_{run_name}_{timestamp}_{suffix}'


def build_session_key(case_id: str, run_id: str, suite: str = DEFAULT_SUITE) -> str:
    return f'bench:{suite}:{case_id}:{run_id}'


def materialize_run(case_dir: Path, run_date: str, run_name: str | None) -> tuple[dict[str, Any], Path]:
    case_config = load_case_config(case_dir)
    case_id = case_config['case_id']
    run_case_root = RUNS_ROOT / run_date / case_id
    run_case_root.mkdir(parents=True, exist_ok=True)

    resolved_run_name = run_name or next_run_name(run_case_root)
    run_dir = run_case_root / resolved_run_name
    if run_dir.exists():
        raise FileExistsError(f'run directory already exists: {run_dir}')

    workspace_source = case_dir / case_config['workspace_source']
    workspace_target = run_dir / 'workspace'
    prompt_path = case_dir / case_config['prompt_file']
    run_id = build_run_id(run_date, resolved_run_name)

    run_dir.mkdir(parents=True, exist_ok=False)
    shutil.copytree(
        workspace_source,
        workspace_target,
        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'),
    )
    shutil.copy2(prompt_path, run_dir / 'prompt.txt')

    sidecars = case_config.get('run_sidecars', [])
    if sidecars is None:
        sidecars = []
    if not isinstance(sidecars, list):
        raise ValueError('run_sidecars must be a list when present')
    for entry in sidecars:
        if not isinstance(entry, dict):
            raise ValueError('run_sidecars entries must be objects')
        source_rel = entry.get('source')
        target_rel = entry.get('target')
        if not isinstance(source_rel, str) or not source_rel.strip():
            raise ValueError('run_sidecars[].source must be a non-empty string')
        if not isinstance(target_rel, str) or not target_rel.strip():
            raise ValueError('run_sidecars[].target must be a non-empty string')

        source_path = case_dir / source_rel
        target_path = run_dir / target_rel
        if source_path.is_dir():
            shutil.copytree(
                source_path,
                target_path,
                ignore=shutil.ignore_patterns('__pycache__', '*.pyc'),
            )
        elif source_path.is_file():
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)
        else:
            raise FileNotFoundError(f'missing run sidecar source: {source_path}')

    metadata = {
        'case_id': case_id,
        'run_id': run_id,
        'run_name': resolved_run_name,
        'run_date': run_date,
        'case_dir': str(case_dir.resolve()),
        'run_dir': str(run_dir.resolve()),
        'workspace_dir': str(workspace_target.resolve()),
        'prompt_file': str((run_dir / 'prompt.txt').resolve()),
        'created_at_epoch': time.time(),
        'status': 'prepared',
    }
    write_json(run_dir / 'metadata.json', metadata)
    return case_config, run_dir


def write_transcript(run_dir: Path, prompt: str, assistant_text: str) -> None:
    transcript_path = run_dir / 'transcript.jsonl'
    lines = [json.dumps({'role': 'user', 'content': prompt}, ensure_ascii=False)]
    if assistant_text:
        lines.append(json.dumps({'role': 'assistant', 'content': assistant_text}, ensure_ascii=False))
    transcript_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


