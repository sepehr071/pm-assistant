from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion, ChatCompletionChunk

from config import Settings

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterClient:
    def __init__(self, settings: Settings) -> None:
        self._client = AsyncOpenAI(
            base_url=OPENROUTER_BASE_URL,
            api_key=settings.openrouter_api_key,
        )
        self._default_headers: dict[str, str] = {
            "HTTP-Referer": settings.openrouter_app_url,
            "X-Title": settings.openrouter_app_title,
        }

    async def chat_stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        **kwargs: Any,
    ) -> AsyncIterator[ChatCompletionChunk]:
        params: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "extra_headers": self._default_headers,
            **kwargs,
        }
        # Opt into per-turn usage accounting on streamed calls. OpenAI's
        # `stream_options.include_usage` makes the SDK surface a final
        # usage-only chunk; OpenRouter additionally needs `usage.include`
        # in extra_body to attach `cost` to that usage object. Merge rather
        # than overwrite so callers can still pass their own stream_options
        # / extra_body (e.g. provider routing, fallback models).
        stream_options = dict(params.get("stream_options") or {})
        stream_options.setdefault("include_usage", True)
        params["stream_options"] = stream_options

        extra_body = dict(params.get("extra_body") or {})
        usage_opt = dict(extra_body.get("usage") or {})
        usage_opt.setdefault("include", True)
        extra_body["usage"] = usage_opt
        params["extra_body"] = extra_body

        if tools:
            params["tools"] = tools
            params["tool_choice"] = tool_choice
        stream = await self._client.chat.completions.create(**params)
        async for chunk in stream:
            yield chunk

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> ChatCompletion:
        params: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "extra_headers": self._default_headers,
            **kwargs,
        }
        if tools:
            params["tools"] = tools
            params.setdefault("tool_choice", "auto")
        return await self._client.chat.completions.create(**params)

    async def aclose(self) -> None:
        await self._client.close()
