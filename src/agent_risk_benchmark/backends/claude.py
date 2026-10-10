"""Claude Code CLI harness adapter using its non-interactive stream-JSON interface."""
from __future__ import annotations

import os
import shlex
import time

from .base import BackendContext, BackendExecution, HarnessBackend, wait_for_process
from .claude_trace import normalize_claude_trace

DEFAULT_CLAUDE_IMAGE = 'agent-risk-claude:v1.0'
CLAUDE_ALLOWED_TOOLS = 'Bash,Read,Write,Edit,Glob,Grep'


class ClaudeBackend(HarnessBackend):
    name = 'claude'
    default_image = DEFAULT_CLAUDE_IMAGE
    workspace_path = '/workspace'

    def container_environment(self, context: BackendContext) -> dict[str, str]:
        env_vars = {
            'BRAVE_API_KEY': os.environ.get('BRAVE_API_KEY', 'placeholder'),
            'DISABLE_AUTOUPDATER': '1',
        }
        if context.mcp_url:
            env_vars['AGENT_RISK_MCP_URL'] = self._container_mcp_url(context)
        if context.model:
            env_vars['ANTHROPIC_MODEL'] = context.model

        base_url = str(
            context.backend_config.get('base_url')
            or context.backend_config.get('baseUrl')
            or os.environ.get('ANTHROPIC_BASE_URL', '')
        ).strip()
        if base_url:
            env_vars['ANTHROPIC_BASE_URL'] = base_url

        auth_token = os.environ.get('ANTHROPIC_AUTH_TOKEN', '').strip()
        api_key = os.environ.get('ANTHROPIC_API_KEY', '').strip()
        if auth_token:
            env_vars['ANTHROPIC_AUTH_TOKEN'] = auth_token
        if api_key:
            env_vars['ANTHROPIC_API_KEY'] = api_key
        if context.model_api_key and not (auth_token or api_key):
            key_name = 'ANTHROPIC_AUTH_TOKEN' if base_url else 'ANTHROPIC_API_KEY'
            env_vars[key_name] = context.model_api_key

        option_env_names = {
            'max_output_tokens': 'CLAUDE_CODE_MAX_OUTPUT_TOKENS',
            'maxOutputTokens': 'CLAUDE_CODE_MAX_OUTPUT_TOKENS',
            'max_thinking_tokens': 'MAX_THINKING_TOKENS',
            'maxThinkingTokens': 'MAX_THINKING_TOKENS',
            'max_retries': 'CLAUDE_CODE_MAX_RETRIES',
            'maxRetries': 'CLAUDE_CODE_MAX_RETRIES',
        }
        for option_name, env_name in option_env_names.items():
            value = context.backend_config.get(option_name)
            if value is not None and str(value).strip():
                env_vars[env_name] = str(value).strip()
        for env_name in (
            'CLAUDE_CODE_MAX_OUTPUT_TOKENS',
            'MAX_THINKING_TOKENS',
            'CLAUDE_CODE_MAX_RETRIES',
        ):
            value = os.environ.get(env_name, '').strip()
            if value:
                env_vars[env_name] = value
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

    def prepare(self, runtime, context: BackendContext) -> None:  # noqa: ANN001
        check = runtime.exec(context.container_name, 'command -v claude', check=False)
        if check.returncode != 0:
            raise RuntimeError('Claude Code CLI is not installed in the selected container image')
        runtime.exec(context.container_name, f'chmod -R u+w {self.workspace_path}')
        if context.mcp_url:
            runtime.exec(
                context.container_name,
                'claude mcp remove --scope user agent-risk-tools',
                check=False,
            )
            runtime.exec(context.container_name, 'claude mcp add --transport http --scope user agent-risk-tools "$AGENT_RISK_MCP_URL"')

    def execute(self, runtime, context: BackendContext) -> BackendExecution:  # noqa: ANN001
        started = time.time()
        raw_path = context.run_dir / 'claude-events.log'
        model_arg = f'--model {shlex.quote(context.model)} ' if context.model else ''
        allowed_tools = CLAUDE_ALLOWED_TOOLS + (',mcp__agent-risk-tools__*' if context.mcp_url else '')
        command = (
            'claude -p --output-format stream-json --verbose '
            '--no-session-persistence --disable-slash-commands '
            '--permission-mode dontAsk '
            f'--allowedTools {shlex.quote(allowed_tools)} '
            f'{model_arg}'
            '< /run_dir/prompt.txt'
        )
        agent = runtime.exec_background(
            context.container_name,
            command,
            raw_path,
            workdir=self.workspace_path,
        )
        timed_out = wait_for_process(agent, context.timeout_sec)
        token_usage = normalize_claude_trace(raw_path, context.run_dir / 'bench-run.jsonl')
        return BackendExecution(
            elapsed_sec=round(time.time() - started, 3),
            token_usage=token_usage,
            processes=[agent],
            timed_out=timed_out,
        )
