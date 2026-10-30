from __future__ import annotations

import logging
import os
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


logger = logging.getLogger("spider_agent.model_gateway")


@dataclass(frozen=True)
class ModelRetryConfig:
    attempts: int = 20
    min_wait: float = 10.0
    max_wait: float = 120.0


_RETRY_CONFIG: ContextVar[ModelRetryConfig] = ContextVar(
    "spider_agent_deepseek_retry_config", default=ModelRetryConfig()
)

DEEPSEEK_ALIASES = {
    "deepseek": "deepseek/deepseek-v4-flash",
    "deepseek-v4-flash": "deepseek/deepseek-v4-flash",
    "deepseek/deepseek-v4-flash": "deepseek/deepseek-v4-flash",
    "deepseek-v4-pro": "deepseek/deepseek-v4-pro",
    "deepseek/deepseek-v4-pro": "deepseek/deepseek-v4-pro",
}


def resolve_model_name(model: str) -> str:
    normalized = model.strip()
    return DEEPSEEK_ALIASES.get(normalized.lower(), normalized)


def configure_model_retries(*, attempts: int, min_wait: float, max_wait: float) -> None:
    if attempts < 1:
        raise ValueError("model retry attempts must be at least 1")
    if min_wait < 0 or max_wait < min_wait:
        raise ValueError("model retry waits must satisfy 0 <= min_wait <= max_wait")
    _RETRY_CONFIG.set(
        ModelRetryConfig(attempts=attempts, min_wait=min_wait, max_wait=max_wait)
    )


def _flatten_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    flattened: list[dict[str, str]] = []
    for message in messages:
        content = message.get("content", "")
        if isinstance(content, list):
            text = "\n".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        else:
            text = str(content)
        flattened.append({"role": str(message.get("role", "user")), "content": text})
    return flattened


def call_current_deepseek(payload: dict[str, Any]) -> tuple[bool, str]:
    """Call the same registered DeepSeek backend used by the formal benchmark."""
    import litellm
    from sweagent.text2sql.evaluator import _ensure_litellm_registry, _litellm_completion_kwargs

    model = resolve_model_name(str(payload["model"]))
    if not model.startswith("deepseek/"):
        raise ValueError(f"Not a registered DeepSeek model: {model}")
    _ensure_litellm_registry(litellm)
    provider_kwargs = _litellm_completion_kwargs(model)
    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": _flatten_messages(list(payload.get("messages") or [])),
        "max_tokens": int(payload.get("max_tokens") or 65536),
        "temperature": float(payload.get("temperature") or 0.0),
        "timeout": float(os.getenv("SPIDER_AGENT_MODEL_TIMEOUT", "300")),
        **provider_kwargs,
    }
    if payload.get("top_p") is not None:
        request_kwargs["top_p"] = payload["top_p"]

    retry = _RETRY_CONFIG.get()
    last_error: Exception | None = None
    for attempt in range(retry.attempts):
        try:
            response = litellm.completion(**request_kwargs)
            content = response.choices[0].message.content or ""
            return True, str(content)
        except Exception as exc:
            last_error = exc
            lowered = str(exc).lower()
            if "context" in lowered and ("length" in lowered or "window" in lowered):
                raise RuntimeError("context_length_exceeded") from exc
            logger.warning(
                "DeepSeek request failed on attempt %d/%d: %s",
                attempt + 1,
                retry.attempts,
                exc,
            )
            if attempt + 1 < retry.attempts:
                wait_seconds = min(retry.max_wait, retry.min_wait * (2 ** attempt))
                time.sleep(wait_seconds)
    raise RuntimeError(
        f"DeepSeek request failed after {retry.attempts} attempts"
    ) from last_error


def call_current_gpt55(payload: dict[str, Any]) -> tuple[bool, str]:
    """Use SWE-agent's GPT-5.5 output cap, sampling, and retry policy."""
    from openai import OpenAI

    client = OpenAI(timeout=float(os.getenv("SPIDER_AGENT_MODEL_TIMEOUT", "300")), max_retries=0)
    retry = _RETRY_CONFIG.get()
    last_error: Exception | None = None
    for attempt in range(retry.attempts):
        try:
            response = client.chat.completions.create(
                model="gpt-5.5",
                messages=_flatten_messages(list(payload.get("messages") or [])),
                max_completion_tokens=int(payload.get("max_tokens") or 128000),
            )
            content = response.choices[0].message.content
            if not content:
                raise RuntimeError("GPT-5.5 returned no text content")
            return True, str(content)
        except Exception as exc:
            last_error = exc
            logger.warning("GPT-5.5 request failed on attempt %d/%d: %s", attempt + 1, retry.attempts, exc)
            if attempt + 1 < retry.attempts:
                time.sleep(min(retry.max_wait, retry.min_wait * (2 ** attempt)))
    raise RuntimeError(f"GPT-5.5 request failed after {retry.attempts} attempts") from last_error


def install_model_gateway(upstream_agent_module: Any) -> None:
    """Route models needing benchmark-specific API parameters."""
    if getattr(upstream_agent_module, "_buysideflow_gateway_installed", False):
        return
    original_call_llm = upstream_agent_module.call_llm

    def routed_call_llm(payload: dict[str, Any]) -> tuple[bool, str]:
        model = resolve_model_name(str(payload.get("model") or ""))
        routed_payload = {**payload, "model": model}
        if model.startswith("deepseek/"):
            return call_current_deepseek(routed_payload)
        if model == "gpt-5.5":
            return call_current_gpt55(routed_payload)
        return original_call_llm(routed_payload)

    upstream_agent_module.call_llm = routed_call_llm
    upstream_agent_module._buysideflow_gateway_installed = True
