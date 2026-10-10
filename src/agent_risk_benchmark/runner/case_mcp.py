"""Launch an MCP server supplied by an individual benchmark case."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

DEFAULT_ENDPOINT = "/mcp"


def resolve_case_mcp_entry(case_dir: Path, case_config: dict[str, Any]) -> Path:
    """Resolve and validate the case-local MCP server entrypoint."""

    config = case_config.get("mcp")
    if not isinstance(config, dict):
        raise ValueError(f"case {case_config.get('case_id', case_dir.name)!r} does not declare mcp.entry")
    entry = config.get("entry")
    if not isinstance(entry, str) or not entry.strip():
        raise ValueError("case mcp.entry must be a non-empty relative path")

    resolved_case_dir = case_dir.resolve()
    entry_path = (resolved_case_dir / entry).resolve()
    try:
        entry_path.relative_to(resolved_case_dir)
    except ValueError as exc:
        raise ValueError(f"case mcp.entry must stay inside the case directory: {entry}") from exc
    if not entry_path.is_file():
        raise FileNotFoundError(f"missing case MCP entry: {entry_path}")
    return entry_path


def _case_mcp_endpoint(case_config: dict[str, Any]) -> str:
    config = case_config.get("mcp")
    endpoint = config.get("endpoint", DEFAULT_ENDPOINT) if isinstance(config, dict) else DEFAULT_ENDPOINT
    if not isinstance(endpoint, str) or not endpoint.startswith("/"):
        raise ValueError("case mcp.endpoint must start with /")
    return endpoint


def case_mcp_tool_names(case_config: dict[str, Any]) -> tuple[str, ...]:
    """Return the tool allowlist declared by the case, if any."""

    config = case_config.get("mcp")
    tools = config.get("tools", []) if isinstance(config, dict) else []
    if not isinstance(tools, list) or not all(isinstance(name, str) and name.strip() for name in tools):
        raise ValueError("case mcp.tools must be a list of non-empty tool names")
    return tuple(dict.fromkeys(name.strip() for name in tools))


def _free_port(host: str = "0.0.0.0") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


@dataclass
class CaseMCPServerProcess:
    """A running case-owned MCP process and its host/container endpoints."""

    process: subprocess.Popen[str]
    log_file: TextIO
    port: int
    endpoint: str

    @property
    def host_url(self) -> str:
        return f"http://127.0.0.1:{self.port}{self.endpoint}"

    @property
    def container_url(self) -> str:
        return f"http://host.docker.internal:{self.port}{self.endpoint}"

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.log_file.close()


def start_case_mcp_server(
    case_dir: Path,
    case_config: dict[str, Any],
    workspace: Path,
    run_dir: Path,
    timeout_sec: float = 15,
) -> CaseMCPServerProcess:
    """Start the MCP entry declared by a case and wait for its listener."""

    entry_path = resolve_case_mcp_entry(case_dir, case_config)
    endpoint = _case_mcp_endpoint(case_config)
    host = "0.0.0.0"
    port = _free_port(host)
    log_file = (run_dir / "mcp-server.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            sys.executable,
            str(entry_path),
            "--workspace",
            str(workspace),
            "--host",
            host,
            "--port",
            str(port),
            "--endpoint",
            endpoint,
            "--allow-host",
            "host.docker.internal:*",
            "--log-level",
            "INFO",
        ],
        cwd=case_dir,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )
    server = CaseMCPServerProcess(process=process, log_file=log_file, port=port, endpoint=endpoint)
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if process.poll() is not None:
            server.stop()
            detail = (run_dir / "mcp-server.log").read_text(encoding="utf-8", errors="replace")
            raise RuntimeError(f"case MCP server exited during startup:\n{detail[-4000:]}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return server
        except OSError:
            time.sleep(0.1)
    server.stop()
    raise RuntimeError(f"case MCP server did not listen on port {port} within {timeout_sec}s")
