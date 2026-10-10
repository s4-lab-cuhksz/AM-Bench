"""OpenClaw harness adapter."""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import time

from .base import BackendContext, BackendExecution, HarnessBackend, empty_token_usage, wait_for_process

DEFAULT_OPENCLAW_IMAGE = 'lsgoose/ambench-harness-openclaw_2026.7.1:v1.0'
OPENCLAW_WORKSPACE = '/root/.openclaw/workspace'
OPENCLAW_SESSION = 'bench-run'


class OpenClawBackend(HarnessBackend):
    name = 'openclaw'
    default_image = DEFAULT_OPENCLAW_IMAGE
    workspace_path = OPENCLAW_WORKSPACE

    def container_environment(self, context: BackendContext) -> dict[str, str]:
        gateway_token = hashlib.sha256(
            f'agent-risk-gateway:{context.container_name}'.encode('utf-8')
        ).hexdigest()
        env_vars = {
            'BRAVE_API_KEY': os.environ.get('BRAVE_API_KEY', 'placeholder'),
            'OPENCLAW_GATEWAY_TOKEN': gateway_token,
        }
        if context.model_api_key:
            env_vars['MODEL_API_KEY'] = context.model_api_key
        if context.mcp_url:
            env_vars['AGENT_RISK_MCP_URL'] = self._container_mcp_url(context)
        option_env_names = {
            'provider': 'OPENCLAW_PROVIDER',
            'base_url': 'OPENCLAW_BASE_URL',
            'baseUrl': 'OPENCLAW_BASE_URL',
            'api': 'OPENCLAW_API',
            'model_id': 'OPENCLAW_MODEL_ID',
            'modelId': 'OPENCLAW_MODEL_ID',
        }
        for option_name, env_name in option_env_names.items():
            value = context.backend_config.get(option_name)
            if value is not None and str(value).strip():
                env_vars[env_name] = str(value).strip()
        for key in (
            'LITELLM_PROXY_BASE',
            'LITELLM_PROXY_ANTHROPIC_BASE',
            'LITELLM_PROXY_GEMINI_BASE',
            'OFOX_ANTHROPIC_API',
        ):
            value = os.environ.get(key, '').strip()
            if value:
                env_vars[key] = value
        return env_vars

    @staticmethod
    def _uses_host_network(context: BackendContext) -> bool:
        configured = context.backend_config.get('network_mode')
        return str(configured).strip() == 'host' if configured is not None else False

    def _container_mcp_url(self, context: BackendContext) -> str:
        url = context.mcp_url or ''
        return url.replace('host.docker.internal', '127.0.0.1') if self._uses_host_network(context) else url

    def container_run_args(self, context: BackendContext) -> list[str]:
        if self._uses_host_network(context):
            return ['--network', 'host']
        return []

    def mount_run_dir(self, context: BackendContext) -> bool:
        return False

    def prepare(self, runtime, context: BackendContext) -> None:  # noqa: ANN001
        runtime.exec(context.container_name, f'chmod -R u+w {self.workspace_path}')
        runtime.exec(
            context.container_name,
            'rm -f '
            '/root/.openclaw/openclaw.json '
            '/root/.openclaw/openclaw.json.bak '
            '/root/.openclaw/openclaw.json.bak.1 '
            '/root/.openclaw/openclaw.json.last-good '
            '/root/.openclaw/agents/main/agent/models.json '
            '/root/.openclaw/agents/main/agent/auth-profiles.json '
            '&& rm -rf /root/.openclaw/agents/main/sessions '
            '&& mkdir -p /root/.openclaw/agents/main/agent /root/.openclaw/agents/main/sessions',
        )
        effective_model = context.model or 'moonshot/kimi-k2.5'
        init_env = f'OPENCLAW_MODEL={shlex.quote(effective_model)}'
        for env_name, key in (
            ('TEMPERATURE', 'temperature'),
            ('TOP_P', 'topP'),
            ('MAX_TOKENS', 'maxTokens'),
        ):
            value = context.llm_params.get(key)
            if value is not None and str(value).strip():
                init_env += f' {env_name}={shlex.quote(str(value).strip())}'
        use_proxy = os.environ.get('OPENCLAW_USE_LITELLM_PROXY', '').strip().lower() in {'1', 'true', 'yes'}
        init_bin = 'openclaw-init-proxy' if use_proxy else 'openclaw-init'
        runtime.exec(context.container_name, f'{init_env} {init_bin}')
        self._configure_mcp(runtime, context)

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
        if context.mcp_tool_names:
            included_tools = ','.join(context.mcp_tool_names)
            runtime.exec(
                context.container_name,
                f'openclaw mcp tools agent-risk-tools --include {shlex.quote(included_tools)}',
            )

    def execute(self, runtime, context: BackendContext) -> BackendExecution:  # noqa: ANN001
        from agent_risk_benchmark.runner.tools import sum_trace_tokens

        started = time.time()
        gateway = runtime.exec_background(
            context.container_name,
            'openclaw gateway',
            context.run_dir / 'gateway.log',
            workdir=self.workspace_path,
        )
        time.sleep(4)
        thinking = str(context.backend_config.get('thinking') or '').strip()
        thinking_arg = f'--thinking {shlex.quote(thinking)} ' if thinking else ''
        command = (
            f'openclaw agent --session-id {OPENCLAW_SESSION} '
            f'{thinking_arg}'
            f'--timeout {context.timeout_sec} --message {shlex.quote(context.prompt)}'
        )
        agent = runtime.exec_background(
            context.container_name,
            command,
            context.run_dir / 'agent.log',
            workdir=self.workspace_path,
        )
        timed_out = wait_for_process(agent, context.timeout_sec + 30)
        trace_path = context.run_dir / 'bench-run.jsonl'
        copied = runtime.copy_from(
            context.container_name,
            f'/root/.openclaw/agents/main/sessions/{OPENCLAW_SESSION}.jsonl',
            trace_path,
        )
        token_usage = sum_trace_tokens(trace_path) if copied else empty_token_usage()
        return BackendExecution(
            elapsed_sec=round(time.time() - started, 3),
            token_usage=token_usage,
            processes=[agent, gateway],
            timed_out=timed_out,
        )
