from __future__ import annotations

from pathlib import Path

from agent_risk_benchmark.backends.base import BackendContext
from agent_risk_benchmark.backends.nanobot import NanobotBackend
from agent_risk_benchmark.backends.nanobot_telemetry import NanobotTelemetryBackend
from agent_risk_benchmark.backends.nullclaw import NullClawBackend


def _context(tmp_path: Path) -> BackendContext:
    return BackendContext(
        container_name="test-container",
        case_id="test-case",
        run_dir=tmp_path,
        workspace_dir=tmp_path / "workspace",
        image="test-image",
        model="test/model",
        model_api_key="test-key",
        llm_params={},
        backend_config={"network_mode": "host"},
        prompt="test prompt",
        timeout_sec=30,
        mcp_url="http://host.docker.internal:12345/mcp",
    )


def test_nanobot_preserves_container_host_mcp_url(tmp_path: Path) -> None:
    config = NanobotBackend.render_config(
        _context(tmp_path),
        mcp_host_cidrs=("172.17.0.1/32",),
    )

    tools = config["tools"]
    assert tools["mcpServers"]["agent-risk-tools"]["url"] == (
        "http://host.docker.internal:12345/mcp"
    )
    assert tools["ssrfWhitelist"] == ["127.0.0.0/8", "172.17.0.1/32"]


def test_nullclaw_preserves_container_host_mcp_url(tmp_path: Path) -> None:
    config = NullClawBackend.render_config(_context(tmp_path))

    assert config["mcp_servers"]["agent-risk-tools"]["url"] == (
        "http://host.docker.internal:12345/mcp"
    )


def test_nanobot_telemetry_preserves_container_host_mcp_url(tmp_path: Path) -> None:
    config = NanobotTelemetryBackend.render_config(
        _context(tmp_path),
        mcp_host_cidrs=("172.17.0.1/32",),
    )

    assert config["tools"]["mcpServers"]["agent-risk-tools"]["url"] == (
        "http://host.docker.internal:12345/mcp"
    )
