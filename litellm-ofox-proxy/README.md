# LiteLLM OFOX Proxy

> 中文文档：[README.zh-CN.md](./README.zh-CN.md)

This directory runs a **LiteLLM Proxy** that forwards OpenAI-compatible (and vendor-specific) chat requests to **[Ofox](https://api.ofox.io)**. Model aliases and upstream bases are declared in `config.yaml`.

The proxy pattern is the same idea as the older standalone [BerriAI/liteLLM-proxy](https://github.com/BerriAI/liteLLM-proxy) project (that repo’s README notes deprecation in favor of the main LiteLLM project; new work generally tracks **[BerriAI/litellm](https://github.com/BerriAI/litellm)**). This repo pins behavior with a small custom callback for reproducible benchmark runs.

## What you can control at the relay

All traffic through this proxy passes a **pre-call hook** (`inject_thinking_params.py`) registered in `config.yaml` under `litellm_settings.callbacks`. The hook is the single place to enforce or tune:

| Concern | Where it is applied | Current behavior (see code) |
|--------|---------------------|-------------------------------|
| **temperature** | Overwritten on every completion request | Set to `0` for deterministic sampling. |
| **top_p** | Overwritten on every completion request | Set to `0.95`. |
| **Thinking / extended reasoning** | Model-specific fields | **GPT-5.x**: `reasoning_effort: "none"`. **Claude**: `thinking: { type: "disabled" }`. **DeepSeek / Kimi / GLM / Doubao / MiniMax**: `extra_body.thinking` disabled. **Qwen / Bailian**: `extra_body.enable_thinking: false`. **Gemini**: no “off” injection (vendor limitations). |

To change policy for a benchmark matrix, edit **`inject_thinking_params.py`** (or split env-driven logic there). Client-supplied `temperature` / `top_p` / thinking flags may be **overridden** by this hook—by design so OpenClaw and other callers cannot accidentally drift settings.

## Prerequisites

- Python 3 with **`litellm`** installed (proxy CLI is `litellm`). Example:

  ```bash
  pip install 'litellm[proxy]'
  ```

## Environment variables

| Variable | Purpose |
|----------|---------|
| **`OFOX_V1_API_KEY`** | Required for OpenAI-compatible routes in `config.yaml` (`os.environ/OFOX_V1_API_KEY`). `start_proxy.sh` also accepts `OFOX_API_KEY` as a fallback name. |
| **`OFOX_ANTHROPIC_API_KEY`** | Anthropic-style routes; defaults to `OFOX_V1_API_KEY` if unset. |
| **`OFOX_GEMINI_API_KEY`** | Gemini routes; defaults to `OFOX_V1_API_KEY` if unset. |
| **`OFOX_API_KEY`** | Used in `config.yaml` for some Gemini entries when set. |

**Do not commit real keys.** Export them in your shell or a local `.env` that stays untracked.

## Start the proxy

From this directory:

```bash
cd litellm-ofox-proxy
export OFOX_V1_API_KEY="sk-..."   # required
./start_proxy.sh
```

`start_proxy.sh` runs:

```bash
litellm --config ./config.yaml --port 4000 --detailed_debug
```

The relay listens on **`http://127.0.0.1:4000`** by default. Point OpenClaw or any OpenAI-compatible client at that base URL and use the **`model_name`** values from `config.yaml` (for example `openai/gpt-5.4`, `anthropic/claude-sonnet-4.6`).

## OpenAI-compatible usage sketch

```python
import openai

client = openai.OpenAI(
    base_url="http://127.0.0.1:4000/v1",
    api_key="dummy",  # LiteLLM may still expect a key; use your proxy auth if configured
)

resp = client.chat.completions.create(
    model="openai/gpt-5.4",
    messages=[{"role": "user", "content": "Hello"}],
    temperature=0.7,  # may be overridden by inject_thinking_params.py
    top_p=1.0,
)
```

## Files

| File | Role |
|------|------|
| `config.yaml` | `model_list` → Ofox endpoints; registers the custom callback. |
| `inject_thinking_params.py` | LiteLLM `CustomLogger` hook: forces sampling + thinking policy. |
| `start_proxy.sh` | Sets API key env vars and launches `litellm`. |

## Troubleshooting

- **`config.yaml` uses `os.environ/OFOX_V1_API_KEY`** — if the proxy fails with auth errors, confirm that variable is exported in the **same** shell that runs `start_proxy.sh`.
- After changing `inject_thinking_params.py`, restart the proxy process.
