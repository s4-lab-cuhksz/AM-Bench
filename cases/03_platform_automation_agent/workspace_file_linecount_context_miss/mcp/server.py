from __future__ import annotations

import sys
from pathlib import Path

CASE_PARENT = Path(__file__).resolve().parents[2]
if str(CASE_PARENT) not in sys.path:
    sys.path.insert(0, str(CASE_PARENT))

from workspace_file_linecount_common import main_mcp


if __name__ == "__main__":
    raise SystemExit(main_mcp("context_miss"))
