from __future__ import annotations

import sys
from pathlib import Path

CASES_ROOT = Path(__file__).resolve().parents[2]
if str(CASES_ROOT) not in sys.path:
    sys.path.insert(0, str(CASES_ROOT))

from selected_origin_tasks_common import evaluate_case_result, main_for_evaluator

if __name__ == "__main__":
    raise SystemExit(main_for_evaluator(lambda case_dir, run_dir, case_config: evaluate_case_result("analyze_access_logs", case_dir, run_dir, case_config)))
