from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

OPENAI_COMPATIBLE_PROVIDERS = {"deepseek"}


class ModelRuntimeError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResolvedModel:
    tool: str
    slot: str
    model: str
    provider: str
    provider_config: dict[str, Any]


def resolve_model(config: dict[str, Any], tool: str = "llm", requested_model: str = "") -> ResolvedModel:
    model = requested_model or str(config.get("models", {}).get("llm") or "deepseek-chat")
    return ResolvedModel(
        tool=tool,
        slot="llm",
        model=model,
        provider="deepseek",
        provider_config=config.get("api_providers", {}).get("deepseek", {}),
    )


def call_openai_compatible_chat(
    *,
    resolved: ResolvedModel,
    system_prompt: str,
    user_prompt: str,
    input_data: dict[str, Any],
    config: dict[str, Any],
) -> str:
    if resolved.provider not in OPENAI_COMPATIBLE_PROVIDERS:
        raise ModelRuntimeError(f"{resolved.provider} 暂未接入同步 Chat Completions 调用。")

    api_key = str(resolved.provider_config.get("api_key") or "").strip()
    base_url = chat_completion_base_url(resolved)
    if not api_key:
        raise ModelRuntimeError(f"{resolved.provider} 还没有配置 api_key。")
    if not base_url:
        raise ModelRuntimeError(f"{resolved.provider} 还没有配置 base_url。")

    messages: list[dict[str, Any]] = []
    if system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt.strip()})
    messages.append({"role": "user", "content": build_text_prompt(user_prompt, input_data)})

    payload = {
        "model": resolved.model,
        "messages": messages,
        "temperature": parse_float(input_data.get("temperature"), 0.7),
    }
    if config.get("api_providers", {}).get("common", {}).get("print_model_input"):
        print(json.dumps(payload, ensure_ascii=False))

    request = urllib.request.Request(
        url=f"{base_url}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    opener = build_opener(config, resolved.provider_config)
    try:
        with opener.open(request, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise ModelRuntimeError(f"模型服务返回 HTTP {exc.code}: {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise ModelRuntimeError(f"模型服务连接失败: {exc.reason}") from exc

    try:
        return str(data["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise ModelRuntimeError(f"模型服务响应格式无法解析: {json.dumps(data, ensure_ascii=False)[:500]}") from exc


def build_text_prompt(user_prompt: str, input_data: dict[str, Any], exclude_keys: set[str] | None = None) -> str:
    excluded = {"model", "system_prompt", "user_prompt", "temperature", *(exclude_keys or set())}
    extras = {
        key: value
        for key, value in input_data.items()
        if key not in excluded and value not in ("", None, False)
    }
    if not extras:
        return user_prompt.strip()
    return "\n\n".join(
        [
            user_prompt.strip(),
            "附加参数：",
            json.dumps(extras, ensure_ascii=False, indent=2),
        ]
    ).strip()


def build_opener(config: dict[str, Any], provider_config: dict[str, Any]) -> urllib.request.OpenerDirector:
    proxy = str(config.get("api_providers", {}).get("common", {}).get("proxy") or "").strip()
    if provider_config.get("enable_proxy") and proxy:
        return urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def chat_completion_base_url(resolved: ResolvedModel) -> str:
    base_url = str(resolved.provider_config.get("base_url") or "").strip().rstrip("/")
    if resolved.provider == "dashscope":
        if not base_url or base_url.endswith("/api/v1"):
            return "https://dashscope.aliyuncs.com/compatible-mode/v1"
    return base_url


def parse_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


