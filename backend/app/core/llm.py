"""共享 LLM 客户端（前沿演进 Phase 2/3：NL-to-Factor 与 FinLLM 文本打分共用）。

provider 由 .env 驱动（LLM_PROVIDER=none|ollama|openai）：
- ollama: POST {base}/api/chat（本地默认 127.0.0.1:11434）
- openai: POST {base}/v1/chat/completions（兼容任何 openai 协议服务）

约定：网络失败/响应异常抛 AQPException(ERR_LLM_UNAVAILABLE)，message 只带
异常类型（不泄漏 URL 细节与密钥）；LLM 未配置由调用方先行判断 llm_enabled()。
"""
from __future__ import annotations

from .config import get_settings
from .errors import ERR_LLM_UNAVAILABLE, AQPException


def llm_enabled() -> bool:
    """LLM 是否已配置（LLM_PROVIDER != none）。"""
    return get_settings().LLM_PROVIDER != "none"


def chat(messages: list[dict], *, temperature: float = 0.0) -> str:
    """发送对话，返回首个 choice/message 文本。阻塞调用（放进 to_thread）。"""
    import httpx

    s = get_settings()
    try:
        with httpx.Client(timeout=s.LLM_TIMEOUT_SECONDS) as client:
            if s.LLM_PROVIDER == "ollama":
                r = client.post(f"{s.LLM_BASE_URL.rstrip('/')}/api/chat",
                                json={"model": s.LLM_MODEL, "stream": False,
                                      "options": {"temperature": temperature},
                                      "messages": messages})
                r.raise_for_status()
                return str(r.json()["message"]["content"])
            # openai 兼容协议
            base = s.LLM_BASE_URL.rstrip("/")
            url = base if base.endswith("/v1") else f"{base}/v1"
            headers = {"Authorization": f"Bearer {s.LLM_API_KEY}"} \
                if s.LLM_API_KEY else {}
            r = client.post(f"{url}/chat/completions",
                            json={"model": s.LLM_MODEL, "temperature": temperature,
                                  "messages": messages}, headers=headers)
            r.raise_for_status()
            return str(r.json()["choices"][0]["message"]["content"])
    except Exception as e:  # noqa: BLE001 统一信封脱敏
        raise AQPException(ERR_LLM_UNAVAILABLE,
                           f"LLM 调用失败（{s.LLM_PROVIDER}:{s.LLM_MODEL}）："
                           f"{type(e).__name__}") from e
