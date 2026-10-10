"""Benchmark repository path discovery."""

from __future__ import annotations

from pathlib import Path


def is_repo_root(path: Path) -> bool:
    has_runner = (path / 'src' / 'agent_risk_benchmark' / 'runner').is_dir()
    return (path / 'cases').is_dir() and has_runner and (path / 'README.md').exists()


def discover_repo_root() -> Path:
    cwd = Path.cwd().resolve()
    for candidate in [cwd, *cwd.parents]:
        if is_repo_root(candidate):
            return candidate

    module_path = Path(__file__).resolve()
    for candidate in module_path.parents:
        if is_repo_root(candidate):
            return candidate

    raise RuntimeError('could not find benchmark repo root; run `ambench` from the benchmark repository root')


REPO_ROOT = discover_repo_root()
RUNS_ROOT = REPO_ROOT / 'runs'
