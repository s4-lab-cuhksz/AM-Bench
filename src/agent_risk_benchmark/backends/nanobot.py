"""nanobot harness adapter using its public Python SDK."""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import time
from pathlib import Path
from typing import Any

from .base import BackendContext, BackendExecution, HarnessBackend, wait_for_process
from .nanobot_trace import normalize_nanobot_trace

DEFAULT_NANOBOT_IMAGE = 'lsgoose/ambench-harness-nanobot_0.2.2:v1.0'
NANOBOT_WORKSPACE = '/root/.nanobot/workspace'
NANOBOT_PYTHON = '/root/.nanobot/venv/bin/python'


class NanobotBackend(HarnessBackend):
    name = 'nanobot'
    default_image = DEFAULT_NANOBOT_IMAGE
    workspace_path = NANOBOT_WORKSPACE

    def container_environment(self, context: BackendContext) -> dict[str, str]:
        env_vars = {'BRAVE_API_KEY': os.environ.get('BRAVE_API_KEY', 'placeholder')}
        if context.mcp_url:
            env_vars['AGENT_RISK_MCP_URL'] = context.mcp_url
        if context.model_api_key:
            env_vars['NANOBOT_MODEL_API_KEY'] = context.model_api_key
        return env_vars

    def container_run_args(self, context: BackendContext) -> list[str]:
        network_mode = str(context.backend_config.get('network_mode', 'host')).strip()
        return ['--network', network_mode] if network_mode else []

    @staticmethod
    def render_config(context: BackendContext) -> dict[str, Any]:
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
        }
        if context.mcp_url:
            config['tools'] = {
                'mcpServers': {
                    'agent-risk-tools': {
                        'type': 'streamableHttp',
                        'url': context.mcp_url.replace(
                            'host.docker.internal', '127.0.0.1'
                        ),
                    }
                },
                'ssrfWhitelist': ['127.0.0.0/8'],
            }
        return config

    def prepare(self, runtime, context: BackendContext) -> None:  # noqa: ANN001
        check = runtime.exec(
            context.container_name,
            f'{NANOBOT_PYTHON} -c {shlex.quote("from nanobot import Nanobot")}',
            check=False,
        )
        if check.returncode != 0:
            raise RuntimeError('nanobot SDK is not installed in the selected container image')
        runtime.exec(context.container_name, f'chmod -R u+w {self.workspace_path}')

        config_path = context.run_dir / 'nanobot-config.json'
        config_path.write_text(
            json.dumps(self.render_config(context), indent=2, ensure_ascii=False) + '\n',
            encoding='utf-8',
        )
        runner_source = Path(__file__).with_name('nanobot_runner.py')
        (context.run_dir / 'nanobot-runner.py').write_text(
            runner_source.read_text(encoding='utf-8'),
            encoding='utf-8',
        )

    def execute(self, runtime, context: BackendContext) -> BackendExecution:  # noqa: ANN001
        started = time.time()
        session_suffix = hashlib.sha256(context.case_id.encode('utf-8')).hexdigest()[:12]
        command = (
            f'{NANOBOT_PYTHON} /run_dir/nanobot-runner.py '
            '--config /run_dir/nanobot-config.json '
            f'--workspace {self.workspace_path} '
            '--prompt /run_dir/prompt.txt '
            f'--session {shlex.quote(f"bench:{session_suffix}")} '
            '--output /run_dir/nanobot-result.json'
        )
        agent = runtime.exec_background(
            context.container_name,
            command,
            context.run_dir / 'nanobot-agent.log',
            workdir=self.workspace_path,
        )
        timed_out = wait_for_process(agent, context.timeout_sec)
        raw_path = context.run_dir / 'nanobot-result.json'
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
