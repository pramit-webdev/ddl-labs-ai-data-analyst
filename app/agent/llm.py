"""Minimal OpenAI-compatible chat client with retries and tool-call parsing."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import Settings, settings


class LLMError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.retryable = retryable


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str = ""


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item))
        return "\n".join(p for p in parts if p).strip()
    return str(content).strip()


def _retry_delay(response: httpx.Response) -> float:
    """Respect provider rate-limit reset hints instead of blindly retrying."""
    header = response.headers.get("retry-after") or response.headers.get("x-ratelimit-reset-tokens") or ""
    match = re.search(r"(?:(\d+)m)?([\d.]+)s", header)
    if match:
        seconds = float(match.group(2)) + (int(match.group(1) or 0) * 60)
        return min(35.0, max(1.0, seconds + 1.0))
    return 1.6


class LLMClient:
    def __init__(self, cfg: Settings = settings):
        self.cfg = cfg

    @property
    def configured(self) -> bool:
        return self.cfg.llm_configured

    @property
    def model(self) -> str:
        return self.cfg.resolved_model

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 1100,
    ) -> LLMResponse:
        if not self.configured:
            raise LLMError("No LLM provider is configured.", retryable=False)
        url = f"{self.cfg.resolved_base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.cfg.resolved_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        headers = {
            "Authorization": f"Bearer {self.cfg.llm_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        timeout = httpx.Timeout(self.cfg.llm_timeout_seconds, connect=10.0)

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with httpx.Client(timeout=timeout) as client:
                    response = client.post(url, headers=headers, json=payload)
                if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                    wait = _retry_delay(response)
                    time.sleep(wait)
                    last_error = LLMError(
                        f"Provider returned HTTP {response.status_code}.",
                        status_code=response.status_code,
                        retryable=True,
                    )
                    continue
                if response.status_code >= 400:
                    detail = ""
                    try:
                        body = response.json()
                        detail = str(body.get("error", {}).get("message") or body.get("detail") or "")
                    except Exception:
                        detail = response.text[:200]
                    raise LLMError(
                        f"Provider returned HTTP {response.status_code}: {detail[:300]}",
                        status_code=response.status_code,
                    )
                data = response.json()
                choice = (data.get("choices") or [{}])[0]
                message = choice.get("message") or {}
                calls: list[ToolCall] = []
                for index, raw in enumerate(message.get("tool_calls") or []):
                    function = raw.get("function") or {}
                    raw_args = function.get("arguments")
                    if isinstance(raw_args, dict):
                        args = raw_args
                        raw_args_text = json.dumps(raw_args)
                    else:
                        raw_args_text = raw_args or "{}"
                        try:
                            args = json.loads(raw_args_text)
                        except (json.JSONDecodeError, TypeError):
                            args = {}
                    calls.append(
                        ToolCall(
                            id=str(raw.get("id") or f"call_{index}"),
                            name=str(function.get("name") or ""),
                            arguments=args if isinstance(args, dict) else {},
                            raw_arguments=raw_args_text,
                        )
                    )
                return LLMResponse(
                    content=_content_to_text(message.get("content")),
                    tool_calls=calls,
                    model=data.get("model") or self.cfg.resolved_model,
                    usage=data.get("usage") or {},
                )
            except LLMError:
                raise
            except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.8 * (2**attempt))
                    continue
                raise LLMError(f"LLM request failed: {type(exc).__name__}") from exc
            except Exception as exc:
                raise LLMError(f"Unexpected LLM error: {type(exc).__name__}") from exc

        raise LLMError(f"LLM request failed after retries: {last_error}")
