FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
ARG NANOBOT_VERSION=0.2.2

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash ca-certificates curl git python3 python3-pip python3-venv ripgrep \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /root/.nanobot/venv \
    && /root/.nanobot/venv/bin/python -m pip install --no-cache-dir \
        "nanobot-ai==${NANOBOT_VERSION}" \
    && /root/.nanobot/venv/bin/python -c \
        'import importlib.metadata as m; assert m.version("nanobot-ai") == "'"${NANOBOT_VERSION}"'"'

RUN mkdir -p /root/.nanobot/workspace /run_dir

WORKDIR /root/.nanobot/workspace

CMD ["/bin/bash", "-c", "tail -f /dev/null"]
