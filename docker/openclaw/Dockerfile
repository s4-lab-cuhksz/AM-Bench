ARG OPENCLAW_BASE_IMAGE=lsgoose/ambench-harness-openclaw_2026.7.1:v1.0
FROM ${OPENCLAW_BASE_IMAGE}

COPY docker/openclaw-init /usr/local/bin/openclaw-init
COPY docker/openclaw-init-proxy /usr/local/bin/openclaw-init-proxy

RUN chmod 0755 /usr/local/bin/openclaw-init /usr/local/bin/openclaw-init-proxy \
    && rm -f \
        /root/.openclaw/openclaw.json \
        /root/.openclaw/openclaw.json.bak \
        /root/.openclaw/openclaw.json.bak.1 \
        /root/.openclaw/openclaw.json.last-good \
        /root/.openclaw/agents/main/agent/models.json \
        /root/.openclaw/agents/main/agent/auth-profiles.json \
    && rm -rf /root/.openclaw/agents/main/sessions \
    && mkdir -p \
        /root/.openclaw/workspace \
        /root/.openclaw/agents/main/agent \
        /root/.openclaw/agents/main/sessions
