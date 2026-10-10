#!/usr/bin/env bash
# 补跑 runs/<RUN_DATE>/ 下未完成（缺 run5/score.json）的 case，目标是补齐 pass@3（默认 n=5, k=3）。
# 参考 run_openclaw_bench_v2_all_cases.sh：改写 environment.json model 后执行 run-container。
#
# 用法（在仓库根）:
#   ./scripts/run_openclaw_bench_v2_resume_incomplete.sh
#   RUN_DATE=2026-04-23 ./scripts/run_openclaw_bench_v2_resume_incomplete.sh
#   MODEL="openai/gpt-5.4" ./scripts/run_openclaw_bench_v2_resume_incomplete.sh
#   DRY_RUN=1 ./scripts/run_openclaw_bench_v2_resume_incomplete.sh
#
# 环境变量:
#   IMAGE           默认 openclaw-bench:v2.0
#   RUN_DATE        默认当天 YYYY-MM-DD
#   PARALLEL        默认 14
#   PASS_TRIALS     默认 5
#   PASS_K          默认 3
#   PASS_METRIC     默认 full
#   RUN_NAME_PREFIX 默认 bench（与 final_results 命名风格一致）
#   MODEL           可选；不传则取 environment.json 当前 model
#   DRY_RUN         1=只打印未完成 case，不实际执行
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_JSON="${REPO_ROOT}/environment.json"

IMAGE="${IMAGE:-openclaw-bench:v2.0}"
RUN_DATE="${RUN_DATE:-$(date +%F)}"
PARALLEL="${PARALLEL:-14}"
PASS_TRIALS="${PASS_TRIALS:-5}"
PASS_K="${PASS_K:-3}"
PASS_METRIC="${PASS_METRIC:-full}"
RUN_NAME_PREFIX="${RUN_NAME_PREFIX:-bench}"
MODEL="${MODEL:-}"
DRY_RUN="${DRY_RUN:-0}"

if [[ ! -f "$ENV_JSON" ]]; then
  echo "缺少 $ENV_JSON" >&2
  exit 1
fi

slugify() {
  echo "$1" | tr '/:' '__' | tr '.' '_'
}

update_env_model() {
  python3 - "$ENV_JSON" "$1" <<'PY'
import json
import pathlib
import sys

p = pathlib.Path(sys.argv[1])
m = sys.argv[2]
data = json.loads(p.read_text(encoding="utf-8"))
data["model"] = m
p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
}

if [[ -z "${MODEL// }" ]]; then
  MODEL="$(python3 - "$ENV_JSON" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["model"])
PY
)"
fi

SLUG="$(slugify "$MODEL")"
RUN_NAME="${RUN_NAME_PREFIX}-${SLUG}"
RUNS_DIR="${REPO_ROOT}/runs/${RUN_DATE}"
SUMMARY="${RUNS_DIR}/summary_bench_v2_resume_${SLUG}.json"
PASS_DOC="${RUNS_DIR}/pass_metrics_bench_v2_resume_${SLUG}.md"

echo "[info] repo          : ${REPO_ROOT}"
echo "[info] image         : ${IMAGE}"
echo "[info] run_date      : ${RUN_DATE}"
echo "[info] model         : ${MODEL}"
echo "[info] run_name      : ${RUN_NAME}"
echo "[info] parallel      : ${PARALLEL}"
echo "[info] pass_trials/k : ${PASS_TRIALS} / ${PASS_K}"
echo "[info] dry_run       : ${DRY_RUN}"

if [[ ! -d "$RUNS_DIR" ]]; then
  echo "缺少运行目录: $RUNS_DIR" >&2
  exit 1
fi

# 找出“未完成”case：以 <run_name>_run5/score.json 是否存在为准。
mapfile -t INCOMPLETE_CASES < <(
  python3 - "$RUNS_DIR" "$RUN_NAME" <<'PY'
from pathlib import Path
import sys

runs = Path(sys.argv[1])
run_name = sys.argv[2]

case_dirs = sorted([p for p in runs.iterdir() if p.is_dir()])
for c in case_dirs:
    score5 = c / f"{run_name}_run5" / "score.json"
    if not score5.is_file():
        print(c.name)
PY
)

if [[ ${#INCOMPLETE_CASES[@]} -eq 0 ]]; then
  echo "[done] 没有未完成 case（按 run5/score.json 判定）"
  exit 0
fi

echo "[info] 未完成 case 数: ${#INCOMPLETE_CASES[@]}"
printf '%s\n' "${INCOMPLETE_CASES[@]}"

if [[ "$DRY_RUN" == "1" ]]; then
  echo "[done] DRY_RUN=1，未执行 run-container"
  exit 0
fi

BACKUP="${ENV_JSON}.bak.resume_incomplete.$(date +%Y%m%d%H%M%S)"
cp -a "$ENV_JSON" "$BACKUP"
echo "[info] 已备份 environment.json -> $BACKUP"
restore() {
  cp -a "$BACKUP" "$ENV_JSON"
  echo "[info] 已恢复 environment.json"
}
trap restore EXIT

update_env_model "$MODEL"

CASE_ARGS=()
for c in "${INCOMPLETE_CASES[@]}"; do
  CASE_ARGS+=(--case "$c")
done

cd "$REPO_ROOT"
agent-risk-benchmark run-container \
  "${CASE_ARGS[@]}" \
  --image "$IMAGE" \
  --parallel "$PARALLEL" \
  --run-date "$RUN_DATE" \
  --run-name "$RUN_NAME" \
  --summary "$SUMMARY" \
  --pass-trials "$PASS_TRIALS" \
  --pass-sample-k "$PASS_K" \
  --pass-metric "$PASS_METRIC" \
  --pass-doc "$PASS_DOC"

echo "[done] summary : $SUMMARY"
echo "[done] passdoc : $PASS_DOC"

trap - EXIT
restore
