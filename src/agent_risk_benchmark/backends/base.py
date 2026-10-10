"""Backend contracts shared by containerized agent harnesses."""
from __future__ import annotations

import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def empty_token_usage() -> dict[str, int]:
    return {'input': 0, 'output': 0, 'cache_read': 0, 'cache_write': 0, 'total': 0}


@dataclass(frozen=True)
class BackendContext:
    container_name: str
    case_id: str
    run_dir: Path
    workspace_dir: Path
    image: str
    model: str | None
    model_api_key: str
    llm_params: dict[str, str]
    backend_config: dict[str, Any]
    prompt: str
    timeout_sec: int
    mcp_url: str | None = None
    mcp_tool_names: tuple[str, ...] = ()


@dataclass
class BackendExecution:
    elapsed_sec: float
    token_usage: dict[str, int] = field(default_factory=empty_token_usage)
    processes: list[subprocess.Popen[str]] = field(default_factory=list)
    timed_out: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


def wait_for_process(proc: subprocess.Popen[str], timeout_sec: float) -> bool:
    """Wait for a process, returning True when it had to be terminated."""
    deadline = time.time() + timeout_sec
    while proc.poll() is None and time.time() < deadline:
        time.sleep(0.25)
    if proc.poll() is not None:
        return False
    proc.kill()
    proc.wait()
    return True


class HarnessBackend(ABC):
    """Harness-specific behavior used by the generic container runner."""

    name: str
    default_image: str
    workspace_path = '/workspace'

    def container_environment(self, context: BackendContext) -> dict[str, str]:
        return {}

    def container_run_args(self, context: BackendContext) -> list[str]:
        """Additional ``docker run`` arguments required by this harness."""
        return []

    def mount_run_dir(self, context: BackendContext) -> bool:
        """Whether the host run directory should be visible inside the agent container."""
        return True

    @abstractmethod
    def prepare(self, runtime: Any, context: BackendContext) -> None:
        """Configure the harness inside an already-running container."""

    @abstractmethod
    def execute(self, runtime: Any, context: BackendContext) -> BackendExecution:
        """Run one prompt, collect its trace, and return usage information."""
