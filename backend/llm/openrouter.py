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
