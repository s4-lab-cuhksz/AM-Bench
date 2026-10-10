# LiteLLM OFOX 代理

本目录运行 **LiteLLM Proxy**，将 OpenAI 兼容（及各家原生）对话请求转发到 **[Ofox](https://api.ofox.io)**。模型别名与上游地址在 `config.yaml` 中声明。

代理形态与早期的独立项目 [BerriAI/liteLLM-proxy](https://github.com/BerriAI/liteLLM-proxy) 同类（该仓库 README 提示已转向主仓库；新功能以 **[BerriAI/litellm](https://github.com/BerriAI/litellm)** 为准）。本仓库通过自定义回调固定行为，便于评测可复现。

> 英文说明见 [README.md](./README.md)。

## 在中转站可统一控制的项

所有经本代理的流量都会经过 **`inject_thinking_params.py`** 预调用钩子（在 `config.yaml` 的 `litellm_settings.callbacks` 中注册）。这是集中**强制或调整**以下参数的单一入口：

| 项目 | 作用位置 | 当前实现（以代码为准） |
|------|----------|------------------------|
| **temperature** | 每次补全请求会被覆盖 | 固定为 `0`，便于确定性采样。 |
| **top_p** | 每次补全请求会被覆盖 | 固定为 `0.95`。 |
| **思考 / 扩展推理** | 按模型族写入不同字段 | **GPT-5.x**：`reasoning_effort: "none"`。**Claude**：`thinking: { type: "disabled" }`。**DeepSeek / Kimi / GLM / Doubao / MiniMax**：在 `extra_body.thinking` 中关闭。**Qwen / 百炼**：`extra_body.enable_thinking: false`。**Gemini**：受厂商限制，本钩子不注入“关闭思考”。 |

若要改评测矩阵策略，请编辑 **`inject_thinking_params.py`**（或在该文件中按环境变量分支）。客户端传入的 `temperature` / `top_p` / thinking 相关字段可能被钩子**覆盖**——有意为之，避免 OpenClaw 等调用方意外带偏参数。

## 环境依赖

- Python 3，已安装 **`litellm`**（命令行为 `litellm`）。例如：

  ```bash
  pip install 'litellm[proxy]'
  ```

## 环境变量

| 变量 | 说明 |
|------|------|
| **`OFOX_V1_API_KEY`** | OpenAI 兼容路由必填（`config.yaml` 中 `os.environ/OFOX_V1_API_KEY`）。`start_proxy.sh` 也可用 **`OFOX_API_KEY`** 作为回退名。 |
| **`OFOX_ANTHROPIC_API_KEY`** | Anthropic 风格路由；未设置时默认等于 `OFOX_V1_API_KEY`。 |
| **`OFOX_GEMINI_API_KEY`** | Gemini 路由；未设置时默认等于 `OFOX_V1_API_KEY`。 |
| **`OFOX_API_KEY`** | `config.yaml` 中部分 Gemini 条目使用。 |

**勿将真实密钥提交到 Git。** 在 shell 或本地未跟踪的 `.env` 中导出即可。

## 启动代理

在本目录执行：

```bash
cd litellm-ofox-proxy
export OFOX_V1_API_KEY="sk-..."   # 必填
./start_proxy.sh
```

`start_proxy.sh` 实际等价于：

```bash
litellm --config ./config.yaml --port 4000 --detailed_debug
```

默认监听 **`http://127.0.0.1:4000`**。将 OpenClaw 或任意 OpenAI 兼容客户端的 base URL 指到该地址，并使用 `config.yaml` 中的 **`model_name`**（例如 `openai/gpt-5.4`、`anthropic/claude-sonnet-4.6`）。

## OpenAI 兼容调用示例

```python
import openai

client = openai.OpenAI(
    base_url="http://127.0.0.1:4000/v1",
    api_key="dummy",  # LiteLLM 仍可能要求 key；若配置了代理鉴权请改用对应密钥
)

resp = client.chat.completions.create(
    model="openai/gpt-5.4",
    messages=[{"role": "user", "content": "你好"}],
    temperature=0.7,  # 可能被 inject_thinking_params.py 覆盖
    top_p=1.0,
)
```

## 文件说明

| 文件 | 作用 |
|------|------|
| `config.yaml` | `model_list` 指向 Ofox；注册自定义回调。 |
| `inject_thinking_params.py` | LiteLLM `CustomLogger` 钩子：统一采样与 thinking 策略。 |
| `start_proxy.sh` | 设置 API Key 环境变量并启动 `litellm`。 |

## 故障排查

- **`config.yaml` 使用 `os.environ/OFOX_V1_API_KEY`**：若鉴权失败，请确认**运行 `start_proxy.sh` 的同一 shell** 已导出该变量。
- 修改 `inject_thinking_params.py` 后需**重启**代理进程。
