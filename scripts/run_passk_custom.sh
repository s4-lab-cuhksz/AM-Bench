#!/usr/bin/env bash
# 指定模型与 case 列表，跑 pass@k（默认 pass@3，PASS_TRIALS=5）。
# 结构仿照 run_email_reply_passk_matrix.sh：run-container + 备份/恢复 environment.json。
#
# 「5 轮」默认指 pass@k 的 5 次 trial（PASS_TRIALS=5）；若要对整份 CASES 并行重复跑多轮独立实验，设 ROUNDS>1。
#
# 用法（在仓库根）:
#   ./scripts/run_passk_custom.sh
#     （默认 CASE：podcast_publish_full_explicit_006；MODEL 读 environment.json）
#   MODELS="moonshotai/kimi-k2.5" CASES="podcast_publish_full_explicit_006" ./scripts/run_passk_custom.sh
#   MODELS="openai/gpt-5.1 anthropic/claude-sonnet-4.6" \
#     CASES="case_a case_b" \
#     PARALLEL=8 RUN_DATE=2026-04-21 ./scripts/run_passk_custom.sh
#
# 环境变量:
#   MODELS        默认读 environment.json 的 model；也可用环境变量覆盖（空格分隔多个）
#   CASES         默认 podcast_publish_full_explicit_006；可用环境变量覆盖
#   RUN_DATE      默认当天
#   PARALLEL      默认 14
#   ROUNDS        默认 1（>1 时对整份 CASES 并行多轮，run-name 后缀 _rN）
#   RUN_SHARDS    默认 1（仅 ROUNDS=1 时可为 2，将 CASES 均分两路 run-container）
#   PASS_TRIALS   默认 5（pass@k 的 trial 次数，即常说的「5 轮」采样）
#   PASS_K        默认 3（pass@k 的 k）
#   PASS_METRIC   默认 full
#   RUN_NAME_PREFIX  默认 bench（trial：bench-<slug>_runN，与全量矩阵一致）
#   SUMMARY_TAG      默认 bench（summary_bench_passk_*.json / pass_metrics_bench_*.md）
#   PASSK_OUT_DIR    默认 $REPO_ROOT/runs/<RUN_DATE>/（按跑批日期落盘，不覆盖 runs/final_results）
#                    与全量矩阵同名规则：summary_bench_passk_*.json / pass_metrics_bench_*.md
#                    若要写入 final_results：PASSK_OUT_DIR="$REPO_ROOT/runs/final_results"
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_JSON="${REPO_ROOT}/environment.json"
RUN_DATE="${RUN_DATE:-$(date +%F)}"
PARALLEL="${PARALLEL:-7}"
ROUNDS="${ROUNDS:-1}"
RUN_SHARDS="${RUN_SHARDS:-1}"
PASS_TRIALS="${PASS_TRIALS:-5}"
PASS_K="${PASS_K:-3}"
PASS_METRIC="${PASS_METRIC:-full}"
RUN_NAME_PREFIX="${RUN_NAME_PREFIX:-bench}"
SUMMARY_TAG="${SUMMARY_TAG:-bench}"
PASSK_OUT_DIR="${PASSK_OUT_DIR:-${REPO_ROOT}/runs/${RUN_DATE}}"

MODELS="${MODELS:-}"
CASES="${CASES:-community_campaign_publish_goal_ambiguity}"

if [[ -z "${MODELS// }" ]] && [[ -f "$ENV_JSON" ]]; then
  MODELS="$(
    ENV_JSON="$ENV_JSON" python3 <<'PY'
import json, os
p = os.environ.get("ENV_JSON", "")
try:
    with open(p, encoding="utf-8") as f:
        m = json.load(f).get("model") or ""
    print(m.strip())
except (OSError, json.JSONDecodeError, TypeError, AttributeError):
    pass
PY
  )"
fi

if [[ -z "${MODELS// }" ]]; then
  echo "错误: 请设置 MODELS，或在 environment.json 中填写 model，例如 MODELS='openai/gpt-5.1' $0" >&2
  exit 1
fi
if [[ -z "${CASES// }" ]]; then
  echo "错误: CASES 为空 $0" >&2
  exit 1
fi

read -r -a MODEL_ARR <<<"$MODELS"
read -r -a CASE_ARR <<<"$CASES"

slugify() {
  echo "$1" | tr '/:' '__' | tr '.' '_'
}

update_env_model() {
  python3 - "$ENV_JSON" "$1" <<'PY'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
m = sys.argv[2]
data = json.loads(p.read_text(encoding="utf-8"))
data["model"] = m
p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
}

if [[ "$ROUNDS" -lt 1 ]]; then
  echo "ROUNDS 须 >= 1（当前: $ROUNDS）" >&2
  exit 1
fi
if [[ "$RUN_SHARDS" -ne 1 ]] && [[ "$RUN_SHARDS" -ne 2 ]]; then
  echo "RUN_SHARDS 只能是 1 或 2（当前: $RUN_SHARDS）" >&2
  exit 1
fi
if [[ "$ROUNDS" -gt 1 ]] && [[ "$RUN_SHARDS" -ne 1 ]]; then
  echo "ROUNDS>1 时不要使用 RUN_SHARDS=2；请设 RUN_SHARDS=1（当前 ROUNDS=$ROUNDS RUN_SHARDS=$RUN_SHARDS）" >&2
  exit 1
fi

if [[ ! -f "$ENV_JSON" ]]; then
  echo "缺少 $ENV_JSON，请先创建 environment.json" >&2
  exit 1
fi

echo "[info] models (${#MODEL_ARR[@]}): ${MODEL_ARR[*]}"
echo "[info] cases (${#CASE_ARR[@]}): ${CASE_ARR[*]}"
echo "[info] PASS_K=${PASS_K} PASS_TRIALS=${PASS_TRIALS} ROUNDS=${ROUNDS} PARALLEL=${PARALLEL}"
echo "[info] PASSK_OUT_DIR=${PASSK_OUT_DIR} SUMMARY_TAG=${SUMMARY_TAG} (summary_${SUMMARY_TAG}_passk_<slug>.json / pass_metrics_${SUMMARY_TAG}_<slug>.md)"

BACKUP="${ENV_JSON}.bak.$(date +%Y%m%d%H%M%S)"
cp -a "$ENV_JSON" "$BACKUP"
echo "[info] 已备份 environment.json -> $BACKUP"
restore() { cp -a "$BACKUP" "$ENV_JSON"; echo "[info] 已恢复 environment.json"; }
trap restore EXIT

cd "$REPO_ROOT"

for model in "${MODEL_ARR[@]}"; do
  SLUG="$(slugify "$model")"
  RUN_NAME="${RUN_NAME_PREFIX}-${SLUG}"

  echo ""
  echo "========== model=${model} run_name=${RUN_NAME} ROUNDS=${ROUNDS} RUN_SHARDS=${RUN_SHARDS} PARALLEL=${PARALLEL} =========="
  update_env_model "$model"

  PARALLEL_PER_SHARD="$PARALLEL"
  if [[ "$RUN_SHARDS" -eq 2 ]] && [[ "$ROUNDS" -eq 1 ]]; then
    PARALLEL_PER_SHARD=$(( PARALLEL / 2 ))
    [[ "$PARALLEL_PER_SHARD" -ge 1 ]] || PARALLEL_PER_SHARD=1
    echo "[info] 拆 case 双路：两批各 --parallel ${PARALLEL_PER_SHARD}"
  elif [[ "$ROUNDS" -gt 1 ]]; then
    echo "[info] 并行 ${ROUNDS} 轮整份 CASES，每轮 --parallel ${PARALLEL}"
  fi

  run_passk_one() {
    local tag="$1"
    shift
    local -a shard=( "$@" )
    if [[ "${#shard[@]}" -eq 0 ]]; then
      return 0
    fi
    local CASE_ARGS=()
    local c
    for c in "${shard[@]}"; do
      CASE_ARGS+=(--case "$c")
    done
    local rn="${RUN_NAME}${tag}"
    mkdir -p "${PASSK_OUT_DIR}"
    local sm="${PASSK_OUT_DIR}/summary_${SUMMARY_TAG}_passk_${SLUG}${tag}.json"
    local pd="${PASSK_OUT_DIR}/pass_metrics_${SUMMARY_TAG}_${SLUG}${tag}.md"
    echo "[info] run-container tag=${tag:-主} cases=${#shard[@]} parallel=${PARALLEL_PER_SHARD}"
    agent-risk-benchmark run-container \
      "${CASE_ARGS[@]}" \
      --parallel "$PARALLEL_PER_SHARD" \
      --run-date "$RUN_DATE" \
      --run-name "$rn" \
      --summary "$sm" \
      --pass-trials "$PASS_TRIALS" \
      --pass-sample-k "$PASS_K" \
      --pass-metric "$PASS_METRIC" \
      --pass-doc "$pd"
  }

  if [[ "$ROUNDS" -gt 1 ]]; then
    ec=0
    pids=()
    for ((round = 1; round <= ROUNDS; round++)); do
      run_passk_one "_r${round}" "${CASE_ARR[@]}" & pids+=($!)
    done
    for pid in "${pids[@]}"; do
      wait "$pid" || ec=1
    done
    [[ "$ec" -eq 0 ]] || echo "[warn] 并行多轮中至少一轮退出码非 0"
  elif [[ "$RUN_SHARDS" -eq 1 ]]; then
    run_passk_one "" "${CASE_ARR[@]}" || echo "[warn] 本轮退出码非 0: $?"
  else
    n=${#CASE_ARR[@]}
    cut=$(( (n + 1) / 2 ))
    SHARD_A=( "${CASE_ARR[@]:0:$cut}" )
    SHARD_B=( "${CASE_ARR[@]:$cut}" )
    ec=0
    pids=()
    run_passk_one "_s1" "${SHARD_A[@]}" & pids+=($!)
    run_passk_one "_s2" "${SHARD_B[@]}" & pids+=($!)
    for pid in "${pids[@]}"; do
      wait "$pid" || ec=1
    done
    [[ "$ec" -eq 0 ]] || echo "[warn] 拆 case 双路中至少一路退出码非 0"
  fi
done

trap - EXIT
restore
