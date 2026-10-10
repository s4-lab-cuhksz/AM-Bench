from __future__ import annotations

import sys
from pathlib import Path

CATEGORY_DIR = Path(__file__).resolve().parents[2]
if str(CATEGORY_DIR) not in sys.path:
    sys.path.insert(0, str(CATEGORY_DIR))

from email_reply_meeting_common import main_mcp


if __name__ == "__main__":
    raise SystemExit(main_mcp("constraint_ambiguity"))
