"""Low-level Docker operations used by harness adapters."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


_LOCAL_PROXY_HOST = re.compile(r'(?i)(?<=://)(localhost|127\.0\.0\.1|\[::1\])(?=[:/]|$)')


def _env_flag(name: str) -> bool:
    return os.environ.get(name, '').lower() in {'1', 'true', 'yes', 'on'}


def _has_network_arg(args: list[str]) -> bool:
    return any(
        arg in {'--network', '--net'} or arg.startswith(('--network=', '--net='))
        for arg in args
    )


def _container_proxy_args() -> list[str]:
    if not _env_flag('AGENT_RISK_FORWARD_PROXY'):
        return [
            '-e', 'http_proxy=',
            '-e', 'https_proxy=',
            '-e', 'all_proxy=',
            '-e', 'HTTP_PROXY=',
            '-e', 'HTTPS_PROXY=',
            '-e', 'ALL_PROXY=',
            '-e', 'no_proxy=',
            '-e', 'NO_PROXY=',
        ]

    args: list[str] = []
    for lower_name in ('http_proxy', 'https_proxy', 'all_proxy'):
        upper_name = lower_name.upper()
        value = os.environ.get(lower_name) or os.environ.get(upper_name)
        if not value:
            continue
        container_value = value
        if not _env_flag('AGENT_RISK_PROXY_HOST_NETWORK'):
            container_value = _LOCAL_PROXY_HOST.sub('host.docker.internal', value)
        args.extend(['-e', f'{lower_name}={container_value}'])
        args.extend(['-e', f'{upper_name}={container_value}'])

    configured_no_proxy = os.environ.get('no_proxy') or os.environ.get('NO_PROXY') or ''
    no_proxy_entries = [entry.strip() for entry in configured_no_proxy.split(',') if entry.strip()]
    for required_host in ('localhost', '127.0.0.1', 'host.docker.internal'):
        if required_host not in no_proxy_entries:
            no_proxy_entries.append(required_host)
    no_proxy_value = ','.join(no_proxy_entries)
    args.extend(['-e', f'no_proxy={no_proxy_value}'])
    args.extend(['-e', f'NO_PROXY={no_proxy_value}'])
    args.extend(['-e', 'NODE_USE_ENV_PROXY=1'])
    return args


class DockerRuntime:
    def __init__(self) -> None:
        self._processes: list[subprocess.Popen[str]] = []

    def start(
        self,
        *,
        name: str,
        workspace_dir: Path,
        run_dir: Path,
        workspace_path: str,
        image: str,
        env_vars: dict[str, str],
        extra_run_args: list[str] | None = None,
        mount_run_dir: bool = True,
    ) -> None:
        env_args: list[str] = []
        for key, value in env_vars.items():
            env_args += ['-e', f'{key}={value}']

        proxy_args = _container_proxy_args()
        resolved_run_args = list(extra_run_args or [])
        if _env_flag('AGENT_RISK_PROXY_HOST_NETWORK') and not _has_network_arg(resolved_run_args):
            resolved_run_args.extend(['--network', 'host'])
        if any(
            'host.docker.internal' in str(value)
            for value in [*env_vars.values(), *proxy_args]
        ):
            resolved_run_args.extend(['--add-host=host.docker.internal:host-gateway'])

        volume_args = ['-v', f'{workspace_dir}:{workspace_path}']
        if mount_run_dir:
            volume_args += ['-v', f'{run_dir}:/run_dir']

        result = subprocess.run(
            [
                'docker', 'run', '-d', '--name', name,
                *resolved_run_args,
                *proxy_args,
                *env_args,
                *volume_args,
                image, '/bin/bash', '-c', 'tail -f /dev/null',
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f'Container start failed:\n{result.stderr}')

    def exec(self, name: str, command: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ['docker', 'exec', name, '/bin/bash', '-c', command],
            capture_output=True,
            text=True,
            check=check,
        )

    def exec_background(
        self,
        name: str,
        command: str,
        log_path: Path,
        *,
        workdir: str | None = None,
    ) -> subprocess.Popen[str]:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = log_path.open('w', encoding='utf-8')
        effective_command = command
        if workdir:
            effective_command = f'cd {workdir} && {command}'
        proc = subprocess.Popen(
            ['docker', 'exec', name, '/bin/bash', '-c', effective_command],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            encoding='utf-8',
        )
        proc._log_file = log_file  # type: ignore[attr-defined]
        self._processes.append(proc)
        return proc

    def copy_from(self, name: str, container_path: str, host_path: Path) -> bool:
        result = subprocess.run(
            ['docker', 'cp', f'{name}:{container_path}', str(host_path)],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def copy_to(self, name: str, host_path: Path, container_path: str) -> bool:
        result = subprocess.run(
            ['docker', 'cp', str(host_path), f'{name}:{container_path}'],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def remove(self, name: str) -> None:
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True)

    def close_process_logs(self) -> None:
        for proc in self._processes:
            log_file = getattr(proc, '_log_file', None)
            if log_file and not log_file.closed:
                log_file.close()
