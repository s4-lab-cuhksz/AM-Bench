FROM node:22-bookworm

ARG CODEX_VERSION=0.144.4

RUN apt-get update \
    && apt-get install -y --no-install-recommends git python3 python3-yaml ripgrep ca-certificates \
    && npm install -g "@openai/codex@${CODEX_VERSION}" \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /workspace /run_dir /root/.codex
WORKDIR /workspace

CMD ["/bin/bash", "-c", "tail -f /dev/null"]
