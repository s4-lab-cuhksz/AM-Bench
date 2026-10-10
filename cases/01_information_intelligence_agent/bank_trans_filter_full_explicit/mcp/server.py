from __future__ import annotations

import sys
from pathlib import Path

CASES_ROOT = Path(__file__).resolve().parents[3]
if str(CASES_ROOT) not in sys.path:
    sys.path.insert(0, str(CASES_ROOT))

from selected_origin_tasks_common import main_mcp

if __name__ == "__main__":
    raise SystemExit(main_mcp("bank_trans_filter", "full_explicit"))
