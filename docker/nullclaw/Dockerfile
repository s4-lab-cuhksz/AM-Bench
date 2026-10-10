# syntax=docker/dockerfile:1

ARG NULLCLAW_BASE_IMAGE=lsgoose/agent-risk-nullclaw:v1.0
FROM ${NULLCLAW_BASE_IMAGE}

ARG NULLCLAW_VERSION=2026.5.29

RUN test "$(nullclaw version)" = "nullclaw ${NULLCLAW_VERSION}" \
    && command -v bash \
    && command -v python3 \
    && mkdir -p /root/.nullclaw/workspace /run_dir

WORKDIR /root/.nullclaw/workspace

CMD ["/bin/bash", "-c", "tail -f /dev/null"]
