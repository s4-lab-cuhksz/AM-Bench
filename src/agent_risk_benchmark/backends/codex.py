"""Codex CLI harness adapter using the official non-interactive JSONL interface."""
from __future__ import annotations

import json
import os
import shlex
import time

from .base import BackendContext, BackendExecution, HarnessBackend, wait_for_process
from .trace import normalize_codex_trace

DEFAULT_CODEX_IMAGE = 'lsgoose/ambench-harness-codex_0.144.4:v1.0'
CODEX_HOME = '/root/.codex'


class CodexBackend(HarnessBackend):
    name = 'codex'
    default_image = DEFAULT_CODEX_IMAGE
    workspace_path = '/workspace'

    def container_environment(self, context: BackendContext) -> dict[str, str]:
        env_vars = {'BRAVE_API_KEY': os.environ.get('BRAVE_API_KEY', 'placeholder')}
        if context.mcp_url:
            env_vars['AGENT_RISK_MCP_URL'] = self._container_mcp_url(context)
        if context.model_api_key:
            env_vars.update(
                {
                    'CODEX_API_KEY': context.model_api_key,
                    'CODEX_MODEL_API_KEY': context.model_api_key,
                    'OPENAI_API_KEY': context.model_api_key,
                }
            )
        return env_vars

    @staticmethod
    def _uses_host_network(context: BackendContext) -> bool:
        configured = context.backend_config.get('network_mode')
        return str(configured).strip() == 'host' if configured is not None else '_local_model' in context.image

    def _container_mcp_url(self, context: BackendContext) -> str:
        url = context.mcp_url or ''
        return url.replace('host.docker.internal', '127.0.0.1') if self._uses_host_network(context) else url

    def container_run_args(self, context: BackendContext) -> list[str]:
        if self._uses_host_network(context):
            return ['--network', 'host']
        return []

    @staticmethod
    def _uses_custom_provider(context: BackendContext) -> bool:
        return bool(
            context.backend_config.get('provider')
            or context.backend_config.get('base_url')
            or (context.model or '').startswith('openrouter/')
        )

    @staticmethod
    def render_provider_config(
        provider: str,
        model: str,
        base_url: str,
        *,
        wire_api: str = 'responses',
    ) -> str:
        """Render a minimal Codex config using TOML-compatible quoted strings."""
        return (
            f'model_provider = {json.dumps(provider)}\n'
            f'model = {json.dumps(model)}\n'
            'approval_policy = "never"\n'
            'sandbox_mode = "danger-full-access"\n\n'
            f'[model_providers.{provider}]\n'
            f'name = {json.dumps(provider.title())}\n'
            f'base_url = {json.dumps(base_url)}\n'
            'env_key = "CODEX_MODEL_API_KEY"\n'
            f'wire_api = {json.dumps(wire_api)}\n'
            'requires_openai_auth = false\n'
            'supports_websockets = false\n'
        )

    @classmethod
    def render_openrouter_config(cls, model: str, base_url: str) -> str:
        """Keep the legacy helper for callers that explicitly select OpenRouter."""
        return cls.render_provider_config('openrouter', model, base_url)

    @staticmethod
    def _provider_settings(context: BackendContext) -> tuple[str, str, str, str]:
        configured_provider = str(context.backend_config.get('provider') or '').strip()
        raw_model = context.model or ''
        provider = configured_provider or ('openrouter' if raw_model.startswith('openrouter/') else '')
        model = str(context.backend_config.get('model_id') or raw_model).strip()
        if provider and model.startswith(f'{provider}/'):
            model = model[len(provider) + 1:]

        default_base_urls = {
            'deepseek': 'https://api.deepseek.com/v1',
            'openrouter': 'https://openrouter.ai/api/v1',
        }
        base_url = str(
            context.backend_config.get('base_url')
            or default_base_urls.get(provider, '')
        ).rstrip('/')
        wire_api = str(context.backend_config.get('wire_api') or 'responses').strip()
        if not provider or not model or not base_url:
            raise ValueError('Codex custom provider requires provider, model, and base_url')
        return provider, model, base_url, wire_api

    @staticmethod
    def _validate_provider_compatibility(provider: str, model: str, base_url: str, wire_api: str) -> None:
        if (
            provider == 'openrouter'
            and model == 'z-ai/glm-5.3'
            and 'api.ofox.io' in base_url
            and wire_api == 'responses'
        ):
            raise RuntimeError(
                'Codex with z-ai/glm-5.3 through api.ofox.io is unsupported: '
                'the provider returns Responses API events that Codex rejects '
                'with "missing input.status parameter", preventing MCP task-tool calls. '
                'Use a Responses-compatible provider/model or a shim that normalizes '
                'the Responses event stream before running this benchmark combination.'
            )

    def prepare(self, runtime, context: BackendContext) -> None:  # noqa: ANN001
        check = runtime.exec(context.container_name, 'command -v codex', check=False)
        if check.returncode != 0:
            raise RuntimeError('Codex CLI is not installed in the selected container image')
        runtime.exec(context.container_name, f'chmod -R u+w {self.workspace_path}')

        if self._uses_custom_provider(context):
            provider, model, base_url, wire_api = self._provider_settings(context)
            self._validate_provider_compatibility(provider, model, base_url, wire_api)
            config = self.render_provider_config(
                provider,
                model,
                base_url,
                wire_api=wire_api,
            )
            config_path = context.run_dir / 'codex-config.toml'
            config_path.write_text(config, encoding='utf-8')
            runtime.exec(
                context.container_name,
                f'mkdir -p {CODEX_HOME} && cp /run_dir/codex-config.toml {CODEX_HOME}/config.toml',
            )

        self._configure_mcp(runtime, context)

    @staticmethod
    def _configure_mcp(runtime, context: BackendContext) -> None:  # noqa: ANN001
        if context.mcp_url:
            runtime.exec(context.container_name, 'codex mcp remove agent-risk-tools', check=False)
            runtime.exec(
                context.container_name, 'codex mcp add agent-risk-tools --url "$AGENT_RISK_MCP_URL"'
            )

    def execute(self, runtime, context: BackendContext) -> BackendExecution:  # noqa: ANN001
        started = time.time()
        raw_path = context.run_dir / 'codex-events.log'
        model = (
            self._provider_settings(context)[1]
            if self._uses_custom_provider(context)
            else (context.model or '')
        )
        model_arg = f'--model {shlex.quote(model)} ' if model else ''
        command = (
            'codex exec --json --skip-git-repo-check '
            '--dangerously-bypass-approvals-and-sandbox --ignore-rules '
            f'{model_arg}'
            '--output-last-message /run_dir/codex-last-message.txt '
            '- < /run_dir/prompt.txt'
        )
        agent = runtime.exec_background(
            context.container_name,
            command,
            raw_path,
            workdir=self.workspace_path,
        )
        timed_out = wait_for_process(agent, context.timeout_sec)
        token_usage = normalize_codex_trace(raw_path, context.run_dir / 'bench-run.jsonl')
        return BackendExecution(
            elapsed_sec=round(time.time() - started, 3),
            token_usage=token_usage,
            processes=[agent],
            timed_out=timed_out,
        )
