FROM node:24-bookworm

ARG CLAUDE_VERSION=2.1.210

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl git python3 python3-yaml ripgrep \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://claude.ai/install.sh | bash -s "${CLAUDE_VERSION}" \
    && ln -sf /root/.local/bin/claude /usr/local/bin/claude \
    && claude --version

RUN mkdir -p /workspace /run_dir /root/.claude

ENV DISABLE_AUTOUPDATER=1

WORKDIR /workspace

CMD ["/bin/bash", "-c", "tail -f /dev/null"]
