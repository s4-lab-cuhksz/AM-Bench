"""Explicit passive-telemetry backend for the Nanobot harness."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import shlex
import time
from pathlib import Path
from typing import Any

from .base import BackendContext, BackendExecution, wait_for_process
from .nanobot import NanobotBackend
from .nanobot_trace import normalize_nanobot_trace

NANOBOT_TELEMETRY_IMAGE = 'agent-risk-nanobot-telemetry:0.2.2-isolation'
NANOBOT_TELEMETRY_WORKSPACE = '/home/nanobot/.nanobot/workspace'
NANOBOT_TELEMETRY_PYTHON = '/app/.venv/bin/python'
NANOBOT_CONTROL_DIR = '/tmp/agent-risk-control'
NANOBOT_CAPTURE_DIR = '/tmp/agent-risk-run'


class NanobotTelemetryBackend(NanobotBackend):
    """Run Nanobot with passive capture isolated from the agent workspace."""

    name = 'nanobot-telemetry'
    default_image = NANOBOT_TELEMETRY_IMAGE
    workspace_path = NANOBOT_TELEMETRY_WORKSPACE

    def mount_run_dir(self, context: BackendContext) -> bool:
        return False

    def container_environment(self, context: BackendContext) -> dict[str, str]:
        env_vars = super().container_environment(context)
        env_vars.update(
            {
                'AP_TELEMETRY_RUN_DIR': NANOBOT_CAPTURE_DIR,
                'AP_NANOBOT_RUNNER': f'{NANOBOT_CONTROL_DIR}/nanobot-runner.py',
            }
        )
        return env_vars

    @staticmethod
    def _container_mcp_url(context: BackendContext) -> str:
        return context.mcp_url or ''

    @staticmethod
    def render_config(
        context: BackendContext,
        *,
        mcp_host_cidrs: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        model = context.model or str(context.backend_config.get('model') or 'qwen3-32b-awq')
        provider = str(context.backend_config.get('provider') or 'custom')
        base_url = str(
            context.backend_config.get('base_url')
            or context.backend_config.get('baseUrl')
            or 'http://127.0.0.1:8001/v1'
        ).rstrip('/')
        api_key: str | None = '${NANOBOT_MODEL_API_KEY}' if context.model_api_key else None
        max_tokens = int(context.llm_params.get('maxTokens') or context.backend_config.get('max_tokens') or 8192)
        context_window = int(context.backend_config.get('context_window_tokens') or 32768)
        temperature = float(context.llm_params.get('temperature') or context.backend_config.get('temperature') or 0.1)
        config = {
            'providers': {provider: {'apiKey': api_key, 'apiBase': base_url}},
            'modelPresets': {
                'benchmark': {
                    'provider': provider,
                    'model': model,
                    'maxTokens': max_tokens,
                    'contextWindowTokens': context_window,
                    'temperature': temperature,
                }
            },
            'agents': {'defaults': {'modelPreset': 'benchmark'}},
            'tools': {'restrictToWorkspace': True},
        }
        if context.mcp_url:
            config['tools'].update(
                {
                    'mcpServers': {
                        'agent-risk-tools': {
                            'type': 'streamableHttp',
                            'url': NanobotTelemetryBackend._container_mcp_url(context),
                        }
                    },
                    'ssrfWhitelist': ['127.0.0.0/8', *mcp_host_cidrs],
                }
            )
        return config

    def prepare(self, runtime, context: BackendContext) -> None:  # noqa: ANN001
        check = runtime.exec(
            context.container_name,
            f'{NANOBOT_TELEMETRY_PYTHON} -c {shlex.quote("from nanobot import Nanobot")}',
            check=False,
        )
        if check.returncode != 0:
            raise RuntimeError('nanobot SDK is not installed in the selected container image')
        runtime.exec(context.container_name, f'chmod -R u+w {self.workspace_path}')

        mcp_host_cidrs: list[str] = []
        if context.mcp_url and 'host.docker.internal' in context.mcp_url:
            resolver = (
                'import socket; '
                'print("\\n".join(sorted({item[4][0] for item in '
                'socket.getaddrinfo("host.docker.internal", None)})))'
            )
            resolved = runtime.exec(
                context.container_name,
                f'{NANOBOT_TELEMETRY_PYTHON} -c {shlex.quote(resolver)}',
                check=False,
            )
            for candidate in (resolved.stdout or '').strip().splitlines():
                try:
                    address = ipaddress.ip_address(candidate)
                    prefix = 32 if address.version == 4 else 128
                    mcp_host_cidrs.append(f'{address}/{prefix}')
                except ValueError:
                    pass

        config_path = context.run_dir / 'nanobot-config.json'
        config_path.write_text(
            json.dumps(
                self.render_config(context, mcp_host_cidrs=tuple(mcp_host_cidrs)),
                indent=2,
                ensure_ascii=False,
            ) + '\n',
            encoding='utf-8',
        )
        runner_source = Path(__file__).with_name('nanobot_runner.py')
        runner_path = context.run_dir / 'nanobot-runner.py'
        runner_path.write_text(runner_source.read_text(encoding='utf-8'), encoding='utf-8')

        runtime.exec(
            context.container_name,
            f'mkdir -p {NANOBOT_CONTROL_DIR} {NANOBOT_CAPTURE_DIR}',
        )
        control_files = {
            config_path: f'{NANOBOT_CONTROL_DIR}/nanobot-config.json',
            runner_path: f'{NANOBOT_CONTROL_DIR}/nanobot-runner.py',
            context.run_dir / 'prompt.txt': f'{NANOBOT_CONTROL_DIR}/prompt.txt',
        }
        metadata_path = context.run_dir / 'metadata.json'
        if metadata_path.exists():
            control_files[metadata_path] = f'{NANOBOT_CAPTURE_DIR}/metadata.json'
        for host_path, container_path in control_files.items():
            if not runtime.copy_to(context.container_name, host_path, container_path):
                raise RuntimeError(f'failed to copy Nanobot control file into container: {host_path.name}')

    def execute(self, runtime, context: BackendContext) -> BackendExecution:  # noqa: ANN001
        started = time.time()
        session_suffix = hashlib.sha256(context.case_id.encode('utf-8')).hexdigest()[:12]
        command = (
            f'{NANOBOT_TELEMETRY_PYTHON} {NANOBOT_CONTROL_DIR}/nanobot-runner.py '
            f'--config {NANOBOT_CONTROL_DIR}/nanobot-config.json '
            f'--workspace {self.workspace_path} '
            f'--prompt {NANOBOT_CONTROL_DIR}/prompt.txt '
            f'--session {shlex.quote(f"bench:{session_suffix}")} '
            f'--output {NANOBOT_CONTROL_DIR}/nanobot-result.json'
        )
        agent = runtime.exec_background(
            context.container_name,
            command,
            context.run_dir / 'nanobot-agent.log',
            workdir=self.workspace_path,
        )
        timed_out = wait_for_process(agent, context.timeout_sec)
        raw_path = context.run_dir / 'nanobot-result.json'
        runtime.copy_from(
            context.container_name,
            f'{NANOBOT_CONTROL_DIR}/nanobot-result.json',
            raw_path,
        )
        runtime.copy_from(
            context.container_name,
            f'{NANOBOT_CAPTURE_DIR}/telemetry',
            context.run_dir / 'telemetry',
        )
        if not timed_out and agent.returncode:
            detail = raw_path.read_text(encoding='utf-8', errors='replace') if raw_path.exists() else ''
            raise RuntimeError(f'nanobot agent failed with exit code {agent.returncode}: {detail}')
        token_usage = (
            normalize_nanobot_trace(raw_path, context.run_dir / 'bench-run.jsonl')
            if raw_path.exists()
            else {}
        )
        return BackendExecution(
            elapsed_sec=round(time.time() - started, 3),
            token_usage=token_usage,
            processes=[agent],
            timed_out=timed_out,
        )
