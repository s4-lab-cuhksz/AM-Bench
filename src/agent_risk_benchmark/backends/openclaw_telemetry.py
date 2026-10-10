"""Explicit passive-telemetry backend for the OpenClaw harness."""
from __future__ import annotations

import json
import shlex

from .base import BackendContext, BackendExecution
from .openclaw import OPENCLAW_SESSION, OpenClawBackend

OPENCLAW_TELEMETRY_IMAGE = 'agent-risk-openclaw-telemetry:0.2.2-isolation'
OPENCLAW_CAPTURE_DIR = '/tmp/agent-risk-run'


class OpenClawTelemetryBackend(OpenClawBackend):
    """Capture OpenClaw's native session after the benchmark agent exits."""

    name = 'openclaw-telemetry'
    default_image = OPENCLAW_TELEMETRY_IMAGE

    def container_environment(self, context: BackendContext) -> dict[str, str]:
        env_vars = super().container_environment(context)
        # The telemetry image normally starts a continuous daemon. Benchmark
        # capture is deliberately post-run so the agent never shares a mounted
        # telemetry directory with that daemon.
        env_vars.update(
            {
                'AP_TELEMETRY_HARNESS': '',
                'AP_TELEMETRY_DRAIN_ATTEMPTS': '0',
            }
        )
        return env_vars

    def _configure_mcp(self, runtime, context: BackendContext) -> None:  # noqa: ANN001
        if not context.mcp_url:
            return
        config = {
            'mcpServers': {
                'agent-risk-tools': {
                    'url': self._container_mcp_url(context),
                }
            }
        }
        config_path = context.run_dir / 'openclaw-mcp.json'
        config_path.write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
        server_json = json.dumps(
            {
                'url': self._container_mcp_url(context),
                'transport': 'streamable-http',
            },
            separators=(',', ':'),
        )
        runtime.exec(context.container_name, 'openclaw mcp unset agent-risk-tools', check=False)
        runtime.exec(
            context.container_name,
            f'openclaw mcp set agent-risk-tools {shlex.quote(server_json)}',
        )

    def execute(self, runtime, context: BackendContext) -> BackendExecution:  # noqa: ANN001
        execution = super().execute(runtime, context)
        runtime.exec(
            context.container_name,
            f'mkdir -p {OPENCLAW_CAPTURE_DIR}',
            check=False,
        )
        metadata_path = context.run_dir / 'metadata.json'
        if metadata_path.exists():
            runtime.copy_to(
                context.container_name,
                metadata_path,
                f'{OPENCLAW_CAPTURE_DIR}/metadata.json',
            )
        source_path = (
            f'/root/.openclaw/agents/main/sessions/{OPENCLAW_SESSION}.jsonl'
        )
        runtime.exec(
            context.container_name,
            'python3 -m active_perception.integrations.telemetry_daemon '
            '--harness openclaw '
            f'--run-dir {OPENCLAW_CAPTURE_DIR} '
            f'--source {shlex.quote(source_path)} '
            '--once',
            check=False,
        )
        runtime.copy_from(
            context.container_name,
            f'{OPENCLAW_CAPTURE_DIR}/telemetry',
            context.run_dir / 'telemetry',
        )
        return execution
