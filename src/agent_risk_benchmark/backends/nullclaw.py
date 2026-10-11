"""NullClaw harness adapter for an OpenAI-compatible local model."""
from __future__ import annotations

import json
import shlex
import shutil
import time
from typing import Any

from .base import BackendContext, BackendExecution, HarnessBackend, wait_for_process
from .nullclaw_trace import normalize_nullclaw_trace

DEFAULT_NULLCLAW_IMAGE = 'lsgoose/ambench-harness-nullclaw_2026.5.29:v1.0'
NULLCLAW_WORKSPACE = '/root/.nullclaw/workspace'
NULLCLAW_SESSION = 'bench-run'


class NullClawBackend(HarnessBackend):
    name = 'nullclaw'
    default_image = DEFAULT_NULLCLAW_IMAGE
    workspace_path = NULLCLAW_WORKSPACE

    def container_environment(self, context: BackendContext) -> dict[str, str]:
        env_vars: dict[str, str] = {}
        if context.mcp_url:
            env_vars['AGENT_RISK_MCP_URL'] = context.mcp_url
        if context.model_api_key:
            env_vars['NULLCLAW_MODEL_API_KEY'] = context.model_api_key
        return env_vars

    def container_run_args(self, context: BackendContext) -> list[str]:
        network_mode = str(context.backend_config.get('network_mode', 'host')).strip()
        return ['--network', network_mode] if network_mode else []

    @staticmethod
    def render_config(context: BackendContext) -> dict[str, Any]:
        model = context.model or str(context.backend_config.get('model') or 'qwen3-32b-awq')
        provider = str(context.backend_config.get('provider') or 'vllm')
        native_tools_value = context.backend_config.get('native_tools')
        if native_tools_value is None:
            native_tools = provider == 'openrouter'
        elif isinstance(native_tools_value, str):
            native_tools = native_tools_value.strip().lower() in {'1', 'true', 'yes', 'on'}
        else:
            native_tools = bool(native_tools_value)
        # NullClaw 2026.5.29 disables native tools on its streaming CLI path.
        api_mode = str(
            context.backend_config.get('api_mode')
            or ('responses' if provider == 'openrouter' else 'chat_completions')
        ).strip()
        runtime_provider = 'openrouter-compatible' if provider == 'openrouter' else provider
        runtime_model = model.removeprefix(f'{provider}/')
        base_url = str(
            context.backend_config.get('base_url')
            or context.backend_config.get('baseUrl')
            or 'http://127.0.0.1:8001/v1'
        ).rstrip('/')
        temperature = float(
            context.llm_params.get('temperature')
            or context.backend_config.get('temperature')
            or 0.1
        )
        return {
            'default_temperature': temperature,
            'models': {
                'providers': {
                    runtime_provider: {
                        'api_key': '${NULLCLAW_MODEL_API_KEY}' if context.model_api_key else 'local',
                        'base_url': base_url,
                        'api_mode': api_mode,
                        'native_tools': native_tools,
                    }
                }
            },
            'agents': {'defaults': {'model': {'primary': f'{runtime_provider}/{runtime_model}'}}},
            'channels': {'cli': True},
            'memory': {'backend': 'none', 'auto_save': False},
            'diagnostics': {'log_tool_calls': True, 'log_llm_io': True},
            'autonomy': {
                'level': 'supervised',
                'workspace_only': True,
                'allowed_commands': ['python', 'python3'],
            },
            **(
                {
                    'mcp_servers': {
                        'agent-risk-tools': {
                            'transport': 'http',
                            'url': context.mcp_url,
                            'timeout_ms': context.timeout_sec * 1000,
                        }
                    }
                }
                if context.mcp_url else {}
            ),
        }

    def prepare(self, runtime, context: BackendContext) -> None:  # noqa: ANN001
        check = runtime.exec(context.container_name, 'command -v nullclaw && nullclaw version', check=False)
        if check.returncode != 0:
            raise RuntimeError('nullclaw is not installed in the selected container image')
        config_path = context.run_dir / 'nullclaw-config.json'
        config_path.write_text(
            json.dumps(self.render_config(context), indent=2, ensure_ascii=False) + '\n',
            encoding='utf-8',
        )
        runtime.exec(
            context.container_name,
            'mkdir -p /root/.nullclaw '
            "&& python3 -c 'import json, os; "
            'src="/run_dir/nullclaw-config.json"; dst="/root/.nullclaw/config.json"; '
            'cfg=json.load(open(src)); '
            'providers=cfg["models"]["providers"]; '
            '[(provider.update({"api_key": os.environ["NULLCLAW_MODEL_API_KEY"]})) '
            'for provider in providers.values() if provider.get("api_key") == "${NULLCLAW_MODEL_API_KEY}"]; '
            "json.dump(cfg, open(dst, \"w\"), indent=2)' "
            '&& chmod 600 /root/.nullclaw/config.json '
            f'&& chmod -R u+w {self.workspace_path} '
            '&& nullclaw config validate',
        )

    def execute(self, runtime, context: BackendContext) -> BackendExecution:  # noqa: ANN001
        started = time.time()
        raw_path = context.run_dir / 'nullclaw-agent.log'
        retry_count = max(0, int(context.backend_config.get('no_response_retries', 1)))
        deadline = started + context.timeout_sec
        processes = []
        timed_out = False

        for attempt in range(1, retry_count + 2):
            remaining_sec = deadline - time.time()
            if remaining_sec <= 0:
                timed_out = True
                break
            session = NULLCLAW_SESSION if attempt == 1 else f'{NULLCLAW_SESSION}-retry{attempt}'
            command = f'nullclaw agent -s {session} -m {shlex.quote(context.prompt)}'
            attempt_path = context.run_dir / f'nullclaw-agent-attempt{attempt}.log'
            agent = runtime.exec_background(
                context.container_name,
                command,
                attempt_path,
                workdir=self.workspace_path,
            )
            processes.append(agent)
            timed_out = wait_for_process(agent, remaining_sec)
            shutil.copyfile(attempt_path, raw_path)
            if timed_out or not agent.returncode:
                break

            detail = attempt_path.read_text(encoding='utf-8', errors='replace')
            retryable_markers = (
                'NoResponseContent',
                'CompatibleApiError',
                'AllProvidersFailed',
            )
            retryable = (
                any(marker in detail for marker in retryable_markers)
                and attempt <= retry_count
            )
            remaining_sec = deadline - time.time()
            if retryable and remaining_sec > 1:
                backoff_sec = min(2 ** (attempt - 1), max(0.0, remaining_sec - 1))
                if backoff_sec > 0:
                    time.sleep(backoff_sec)
                continue
            raise RuntimeError(
                f'nullclaw agent failed with exit code {agent.returncode}: {detail[-4000:]}'
            )

        token_usage = normalize_nullclaw_trace(raw_path, context.run_dir / 'bench-run.jsonl')
        return BackendExecution(
            elapsed_sec=round(time.time() - started, 3),
            token_usage=token_usage,
            processes=processes,
            timed_out=timed_out,
            metadata={'attempt_count': len(processes)},
        )
