from litellm.integrations.custom_logger import CustomLogger
from litellm.proxy.proxy_server import UserAPIKeyAuth, DualCache
import json


def ensure_extra_body(data: dict) -> dict:
    extra_body = data.get("extra_body")
    if not isinstance(extra_body, dict):
        extra_body = {}
        data["extra_body"] = extra_body
    return extra_body


class InjectThinkingParams(CustomLogger):
    async def async_pre_call_hook(
        self,
        user_api_key_dict: UserAPIKeyAuth,
        cache: DualCache,
        data: dict,
        call_type: str,
    ):
        if call_type not in ("completion", "text_completion", "acompletion", "atext_completion"):
            return data

        model = str(data.get("model", "")).lower()
        # Enforce deterministic sampling params for all requests.
        data["temperature"] = 0
        data["top_p"] = 0.95

        # OpenAI GPT-5.1 / GPT-5.4:
        # Chat Completions / OpenAI-compatible route 通常用顶层 reasoning_effort
        if "gpt-5" in model:
            data["reasoning_effort"] = "none"

        # Claude:
        # Anthropic route 可直接顶层传 thinking disabled
        elif "claude" in model:
            data["thinking"] = {"type": "disabled"}

        # DeepSeek / Kimi / GLM / Doubao / MiniMax:
        # 这些现在走 Ofox OpenAI-compatible route，vendor-native 参数放 extra_body 里更稳
        elif any(x in model for x in [
            "deepseek",
            "kimi",
            "moonshot",
            "glm",
            "z-ai",
            "zai",
            "doubao",
            "minimax",
        ]):
            extra_body = ensure_extra_body(data)
            extra_body["thinking"] = {"type": "disabled"}

        # Qwen / Bailian:
        # 百炼/Qwen 通常用 enable_thinking=false
        elif any(x in model for x in ["qwen", "bailian"]):
            extra_body = ensure_extra_body(data)
            extra_body["enable_thinking"] = False

        # Gemini:
        # Gemini 3 / Gemini 2.5 Pro 不能真正关闭 thinking，不注入 off
        elif "gemini" in model:
            pass

        preview = {
            "model": data.get("model"),
            "call_type": call_type,
            "temperature": data.get("temperature"),
            "top_p": data.get("top_p"),
            "reasoning_effort": data.get("reasoning_effort"),
            "thinking": data.get("thinking"),
            "extra_body": data.get("extra_body"),
        }

        print("[LiteLLM pre_call data]", json.dumps(preview, ensure_ascii=False, default=str))
        return data


proxy_handler_instance = InjectThinkingParams()