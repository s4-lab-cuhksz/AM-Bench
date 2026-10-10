from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from agent_risk_benchmark.backends import BACKEND_NAMES, get_backend
from agent_risk_benchmark.backends.codex import CodexBackend
from agent_risk_benchmark.backends.trace import normalize_codex_trace
from agent_risk_benchmark.runner.container import _resolve_container_args


class BackendRegistryTests(unittest.TestCase):
    def test_registered_backends_are_constructible(self) -> None:
        self.assertEqual(BACKEND_NAMES, ('openclaw', 'codex', 'claude'))
        self.assertEqual(get_backend('openclaw').name, 'openclaw')
        self.assertEqual(get_backend('codex').name, 'codex')
        self.assertEqual(get_backend('claude').name, 'claude')

    def test_backend_specific_configuration_is_resolved(self) -> None:
        args = Namespace(backend='codex', container_image=None, model=None, parallel=2)
        config = {
            'container_image': 'legacy-openclaw:latest',
            'backends': {
                'codex': {
                    'image': 'custom-codex:v2',
                    'model': 'gpt-5.4',
                    'model_api_key': 'test-key',
                }
            },
        }

        resolved = _resolve_container_args(args, config)

        self.assertEqual(resolved['backend'], 'codex')
        self.assertEqual(resolved['image'], 'custom-codex:v2')
        self.assertEqual(resolved['model'], 'gpt-5.4')
        self.assertEqual(resolved['model_api_key'], 'test-key')
        self.assertEqual(resolved['parallel'], 2)

    def test_openclaw_custom_provider_options_and_key_env_are_preserved(self) -> None:
        args = Namespace(backend='openclaw', container_image=None, model=None, parallel=1)
        config = {
            'backends': {
                'openclaw': {
                    'image': 'lsgoose/ambench-harness-openclaw_2026.7.1:v1.0',
                    'model': 'company/team/model-v9',
                    'provider': 'company',
                    'model_id': 'team/model-v9',
                    'base_url': 'https://models.example.test/v1',
                    'api': 'openai-completions',
                    'api_key_env': 'COMPANY_MODEL_API_KEY',
                }
            }
        }
        previous = os.environ.get('COMPANY_MODEL_API_KEY')
        os.environ['COMPANY_MODEL_API_KEY'] = 'company-test-key'
        try:
            resolved = _resolve_container_args(args, config)
        finally:
            if previous is None:
                os.environ.pop('COMPANY_MODEL_API_KEY', None)
            else:
                os.environ['COMPANY_MODEL_API_KEY'] = previous

        self.assertEqual(resolved['model_api_key'], 'company-test-key')
        self.assertEqual(resolved['backend_config']['provider'], 'company')
        self.assertEqual(resolved['backend_config']['model_id'], 'team/model-v9')
        self.assertEqual(resolved['backend_config']['base_url'], 'https://models.example.test/v1')


class OpenClawInitTests(unittest.TestCase):
    def run_init(self, extra_env: dict[str, str], script_name: str = 'openclaw-init') -> dict:
        script = Path(__file__).resolve().parents[1] / 'docker' / script_name
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env.update(
                {
                    'HOME': tmp,
                    'OPENCLAW_MODEL': 'custom/model-placeholder',
                    'MODEL_API_KEY': 'must-not-be-written',
                    'OPENCLAW_GATEWAY_TOKEN': 'test-gateway-token',
                }
            )
            env.update(extra_env)
            result = subprocess.run(
                [sys.executable, str(script)],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            config_path = Path(tmp) / '.openclaw' / 'openclaw.json'
            raw_config = config_path.read_text(encoding='utf-8')
            self.assertNotIn('must-not-be-written', raw_config)
            self.assertNotIn('test-gateway-token', raw_config)
            return json.loads(raw_config)

    def test_custom_provider_and_model_are_supported(self) -> None:
        config = self.run_init(
            {
                'OPENCLAW_PROVIDER': 'company-gateway',
                'OPENCLAW_MODEL_ID': 'team/experimental-model-v9',
                'OPENCLAW_BASE_URL': 'https://models.example.test/v1/',
                'OPENCLAW_API': 'openai-completions',
            }
        )

        primary = 'company-gateway/team/experimental-model-v9'
        provider = config['models']['providers']['company-gateway']
        self.assertEqual(config['agents']['defaults']['model']['primary'], primary)
        self.assertEqual(config['models']['mode'], 'replace')
        self.assertEqual(provider['baseUrl'], 'https://models.example.test/v1')
        self.assertEqual(provider['models'][0]['id'], 'team/experimental-model-v9')
        self.assertEqual(
            provider['apiKey'],
            {'source': 'env', 'provider': 'default', 'id': 'MODEL_API_KEY'},
        )
        self.assertEqual(config['gateway']['mode'], 'local')
        self.assertNotIn('models', config['agents']['defaults'])

    def test_proxy_init_supports_a_custom_model(self) -> None:
        config = self.run_init(
            {
                'OPENCLAW_PROVIDER': 'proxy-models',
                'OPENCLAW_MODEL_ID': 'org/new-model',
                'LITELLM_PROXY_BASE': 'http://gateway.test:4000/v1',
            },
            script_name='openclaw-init-proxy',
        )

        provider = config['models']['providers']['proxy-models']
        self.assertEqual(config['gateway']['mode'], 'local')
        self.assertEqual(config['models']['mode'], 'replace')
        self.assertEqual(provider['baseUrl'], 'http://gateway.test:4000/v1')
        self.assertEqual(provider['models'][0]['id'], 'org/new-model')
        self.assertNotEqual(provider['apiKey'], 'must-not-be-written')

    def test_known_provider_uses_its_default_transport(self) -> None:
        config = self.run_init(
            {
                'OPENCLAW_MODEL': 'deepseek/deepseek-chat',
                'OPENCLAW_PROVIDER': '',
                'OPENCLAW_MODEL_ID': '',
                'OPENCLAW_BASE_URL': '',
                'OPENCLAW_API': '',
            }
        )

        provider = config['models']['providers']['deepseek']
        self.assertEqual(provider['baseUrl'], 'https://api.deepseek.com/v1')
        self.assertEqual(provider['api'], 'openai-completions')
        self.assertEqual(provider['models'][0]['id'], 'deepseek-chat')


class CodexConfigTests(unittest.TestCase):
    def test_openrouter_config_is_valid_toml(self) -> None:
        rendered = CodexBackend.render_openrouter_config(
            'anthropic/claude-sonnet-4.6',
            'https://openrouter.ai/api/v1',
        )

        quote = chr(34)
        self.assertIn(f'model_provider = {quote}openrouter{quote}', rendered)
        self.assertIn(f'model = {quote}anthropic/claude-sonnet-4.6{quote}', rendered)
        self.assertIn(f'base_url = {quote}https://openrouter.ai/api/v1{quote}', rendered)
        self.assertIn(f'env_key = {quote}OPENROUTER_API_KEY{quote}', rendered)


class CodexTraceTests(unittest.TestCase):
    def test_command_and_usage_are_normalized_for_existing_oracles(self) -> None:
        events = [
            {'type': 'thread.started', 'thread_id': 'thread-1'},
            {
                'type': 'item.completed',
                'item': {
                    'id': 'item-1',
                    'type': 'command_execution',
                    'command': 'python tools/export_contacts.py',
                    'status': 'completed',
                    'exit_code': 0,
                    'aggregated_output': 'done',
                },
            },
            {
                'type': 'item.completed',
                'item': {'id': 'item-2', 'type': 'agent_message', 'text': 'Finished.'},
            },
            {
                'type': 'turn.completed',
                'usage': {
                    'input_tokens': 100,
                    'cached_input_tokens': 60,
                    'output_tokens': 20,
                },
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / 'codex-events.log'
            output = root / 'bench-run.jsonl'
            raw.write_text(''.join(json.dumps(event) + '\n' for event in events), encoding='utf-8')

            usage = normalize_codex_trace(raw, output)
            records = [json.loads(line) for line in output.read_text(encoding='utf-8').splitlines()]

        self.assertEqual(
            usage,
            {'input': 100, 'output': 20, 'cache_read': 60, 'cache_write': 0, 'total': 120},
        )
        tool_call = records[0]['message']['content'][0]
        self.assertEqual(tool_call['type'], 'toolCall')
        self.assertEqual(tool_call['name'], 'exec')
        self.assertEqual(tool_call['arguments']['command'], 'python tools/export_contacts.py')
        self.assertEqual(records[1]['message']['role'], 'toolResult')
        self.assertEqual(records[2]['message']['content'][0]['text'], 'Finished.')


if __name__ == '__main__':
    unittest.main()
