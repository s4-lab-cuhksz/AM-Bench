#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
namespace="${IMAGE_NAMESPACE:-lsgoose}"
release="${IMAGE_RELEASE:-v1.0}"
push_images="${PUSH:-0}"

images=(
  "openclaw|2026.7.1"
  "codex|0.144.4"
  "nanobot|0.2.2"
  "nullclaw|2026.5.29"
)

cd "$repo_root"

for item in "${images[@]}"; do
  IFS='|' read -r harness version <<<"$item"
  image="${namespace}/ambench-harness-${harness}_${version}:${release}"

  echo "Building ${image}"
  docker build \
    --file "docker/${harness}/Dockerfile" \
    --tag "$image" \
    .

  if [[ "$push_images" == "1" ]]; then
    echo "Pushing ${image}"
    docker push "$image"
  fi
done
