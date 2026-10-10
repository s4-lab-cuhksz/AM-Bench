"""Container harness adapter registry."""
from __future__ import annotations

from .base import BackendContext, BackendExecution, HarnessBackend
from .claude import ClaudeBackend
from .codex import CodexBackend
from .nanobot import NanobotBackend
from .nanobot_telemetry import NanobotTelemetryBackend
from .nullclaw import NullClawBackend
from .openclaw import OpenClawBackend
from .openclaw_telemetry import OpenClawTelemetryBackend

BACKEND_NAMES = (
    'openclaw',
    'openclaw-telemetry',
    'codex',
    'claude',
    'nanobot',
    'nanobot-telemetry',
    'nullclaw',
)
DEFAULT_BACKEND = 'openclaw'


def get_backend(name: str) -> HarnessBackend:
    normalized = name.strip().lower()
    if normalized == 'openclaw':
        return OpenClawBackend()
    if normalized == 'openclaw-telemetry':
        return OpenClawTelemetryBackend()
    if normalized == 'codex':
        return CodexBackend()
    if normalized == 'claude':
        return ClaudeBackend()
    if normalized == 'nanobot':
        return NanobotBackend()
    if normalized == 'nanobot-telemetry':
        return NanobotTelemetryBackend()
    if normalized == 'nullclaw':
        return NullClawBackend()
    raise ValueError(f'unknown backend: {name}. Available backends: {", ".join(BACKEND_NAMES)}')


__all__ = [
    'BACKEND_NAMES',
    'DEFAULT_BACKEND',
    'BackendContext',
    'BackendExecution',
    'HarnessBackend',
    'get_backend',
]
