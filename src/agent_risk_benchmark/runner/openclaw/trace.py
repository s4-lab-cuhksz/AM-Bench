"""OpenClaw session-index polling and trace collection."""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from ..constants import DEFAULT_TRACE_WAIT_SEC, TRACE_POLL_INTERVAL_SEC
from .config import load_json_dict


def load_session_index(path: Path) -> dict[str, Any]:
    return load_json_dict(path, label='OpenClaw sessions index')


def wait_for_session_trace(session_key: str, sessions_index_path: Path, timeout_sec: float = DEFAULT_TRACE_WAIT_SEC) -> dict[str, Any] | None:
    deadline = time.time() + timeout_sec
    while True:
        if sessions_index_path.exists():
            index = load_session_index(sessions_index_path)
            record = index.get(session_key)
            if isinstance(record, dict):
                session_file_raw = record.get('sessionFile')
                if isinstance(session_file_raw, str) and session_file_raw.strip():
                    session_file = Path(session_file_raw).expanduser()
                    if session_file.exists():
                        payload = dict(record)
                        payload['sessionFile'] = str(session_file.resolve())
                        return payload
        if time.time() >= deadline:
            return None
        time.sleep(TRACE_POLL_INTERVAL_SEC)


def copy_openclaw_trace(run_dir: Path, session_key: str, sessions_index_path: Path | None) -> dict[str, Any] | None:
    if sessions_index_path is None:
        return None

    record = wait_for_session_trace(session_key, sessions_index_path)
    if record is None:
        return None

    session_file = Path(record['sessionFile'])
    copied_path = run_dir / session_file.name
    shutil.copy2(session_file, copied_path)
    alias_path = run_dir / 'openclaw_trace.jsonl'
    if alias_path != copied_path:
        shutil.copy2(session_file, alias_path)

    return {
        'session_id': record.get('sessionId'),
        'source': str(session_file),
        'copied_path': str(copied_path),
        'alias_path': str(alias_path),
    }


