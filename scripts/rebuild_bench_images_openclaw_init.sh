#!/usr/bin/env bash
# 用仓库内 docker/openclaw-init 覆盖多个 bench 镜像中的 /usr/local/bin/openclaw-init。
# 与 build_openclaw_bench_v2.sh 相同 Dockerfile 逻辑，但对 BASE 镜像列表逐一重建本地 tag。
#
# 用法（在仓库根，且本机已存在下列基础镜像）:
#   ./scripts/rebuild_bench_images_openclaw_init.sh
#
# 默认覆盖的本地 tag（可通过环境变量 BASE_IMAGES 覆盖，空格分隔）:
#   openclaw-bench:v2.0
#   openclaw-v2.26-bench:v2.0
#   openclaw-v3.8-bench:v2.0
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

if [[ ! -f "docker/openclaw-init" ]]; then
  echo "[error] docker/openclaw-init 不存在" >&2
  exit 1
fi
if [[ ! -f "docker/openclaw-init-proxy" ]]; then
  echo "[error] docker/openclaw-init-proxy 不存在" >&2
  exit 1
fi

# shellcheck disable=SC2206
BASE_IMAGES=(${BASE_IMAGES:-openclaw-bench:v2.0 openclaw-v2.26-bench:v2.0 openclaw-v3.8-bench:v2.0})

for BASE_IMAGE in "${BASE_IMAGES[@]}"; do
  LOCAL_IMAGE="${BASE_IMAGE}"
  echo "[step] build patched image <- base ${BASE_IMAGE} -> tag ${LOCAL_IMAGE}"
  docker build -t "${LOCAL_IMAGE}" -f- "${REPO_ROOT}" <<EOF
FROM ${BASE_IMAGE}
COPY docker/openclaw-init /usr/local/bin/openclaw-init
COPY docker/openclaw-init-proxy /usr/local/bin/openclaw-init-proxy
RUN chmod +x /usr/local/bin/openclaw-init /usr/local/bin/openclaw-init-proxy
RUN set -eux; \\
  if command -v python >/dev/null 2>&1; then \\
    exit 0; \\
  fi; \\
  for p in /usr/bin/python3 /usr/local/bin/python3; do \\
    if [ -x "\$p" ]; then \\
      ln -sfn "\$p" /usr/local/bin/python; \\
      exit 0; \\
    fi; \\
  done; \\
  echo "ERROR: neither python nor python3 found in base image" >&2; \\
  exit 1
EOF
done

echo "[done] 已更新 ${#BASE_IMAGES[@]} 个镜像内的 openclaw-init（本地 tag 已覆盖）"
