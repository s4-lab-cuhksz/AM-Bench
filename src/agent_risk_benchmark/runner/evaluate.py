"""Oracle-based evaluation for completed benchmark runs."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from .cases import resolve_oracle_entry
from .tools import load_case_config


def evaluate_run(case_dir: Path, run_dir: Path) -> dict[str, Any]:
    case_config = load_case_config(case_dir)
    oracle_entry = resolve_oracle_entry(case_dir, case_config)
    output_path = run_dir / 'score.json'
    subprocess.run(
        [
            sys.executable,
            str(oracle_entry),
            '--case-dir',
            str(case_dir),
            '--run-dir',
            str(run_dir),
            '--output',
            str(output_path),
        ],
        cwd=case_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(output_path.read_text(encoding='utf-8'))


