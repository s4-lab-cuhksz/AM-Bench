#!/usr/bin/env bash
# config.yaml 里 OpenAI 兼容路由用的是 os.environ/OFOX_V1_API_KEY，不是 OFOX_API_KEY。
set -euo pipefail
cd "$(dirname "$0")"

: "${OFOX_V1_API_KEY:=${OFOX_API_KEY:?Set OFOX_V1_API_KEY or OFOX_API_KEY before starting the proxy}}"

export OFOX_V1_API_KEY
export OFOX_ANTHROPIC_API_KEY="${OFOX_ANTHROPIC_API_KEY:-$OFOX_V1_API_KEY}"
export OFOX_GEMINI_API_KEY="${OFOX_GEMINI_API_KEY:-$OFOX_V1_API_KEY}"

exec litellm --config ./config.yaml --port 4000 --detailed_debug
