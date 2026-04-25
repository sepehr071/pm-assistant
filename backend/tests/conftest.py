from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Ensure backend/ is on sys.path so tests use the same imports as the app.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# Force an in-memory SQLite DB BEFORE config.py / db/session.py is imported.
# Shared-cache named URI so multiple sessions see the same memory DB.
os.environ.setdefault("OPENROUTER_API_KEY", "test-key-not-used")
os.environ["DATABASE_URL"] = (
    "sqlite+aiosqlite:///file:pm_test?mode=memory&cache=shared&uri=true"
)

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402


# --------- fake streaming chunks used by FakeOpenRouter ---------

@dataclass
class FakeDeltaFunction:
    name: str | None = None
    arguments: str | None = None


@dataclass
class FakeDeltaToolCall:
    index: int = 0
    id: str | None = None
    type: str = "function"
    function: FakeDeltaFunction | None = None


@dataclass
class FakeDelta:
    content: str | None = None
    tool_calls: list[FakeDeltaToolCall] | None = None


@dataclass
class FakeChoice:
    delta: FakeDelta = field(default_factory=FakeDelta)
    finish_reason: str | None = None
    index: int = 0


@dataclass
class FakeChunk:
    choices: list[FakeChoice] = field(default_factory=list)


def token_chunk(text: str, finish: str | None = None) -> FakeChunk:
    return FakeChunk(choices=[FakeChoice(delta=FakeDelta(content=text), finish_reason=finish)])


def tool_call_chunk(
    *,
    index: int,
    id: str | None,
    name: str | None,
    args_piece: str | None,
    finish: str | None = None,
) -> FakeChunk:
    tc = FakeDeltaToolCall(
        index=index,
        id=id,
        function=FakeDeltaFunction(name=name, arguments=args_piece),
    )
    return FakeChunk(
        choices=[FakeChoice(delta=FakeDelta(tool_calls=[tc]), finish_reason=finish)]
    )


def stop_chunk(reason: str = "stop") -> FakeChunk:
    return FakeChunk(choices=[FakeChoice(delta=FakeDelta(), finish_reason=reason)])


# --------- FakeOpenRouter ---------

class FakeOpenRouter:
    """Scripted async OpenRouter-like client."""

    def __init__(self, script: list[list[FakeChunk]] | None = None) -> None:
        self.script: list[list[FakeChunk]] = list(script or [])
        self.calls: list[dict[str, Any]] = []

    async def _iterate(self, chunks: list[FakeChunk]) -> AsyncIterator[FakeChunk]:
        for chunk in chunks:
            await asyncio.sleep(0)
            yield chunk

    def chat_stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[FakeChunk]:
        self.calls.append(
            {"model": model, "messages": list(messages), "tools": tools, "kwargs": kwargs}
        )
        chunks = self.script.pop(0) if self.script else [stop_chunk()]
        return self._iterate(chunks)


# --------- FakeMCPManager ---------

@dataclass
class FakeServerStatus:
    name: str
    healthy: bool = True
    error: str | None = None
    tool_count: int = 0


class FakeMCPManager:
    """Deterministic MCP manager double."""

    def __init__(
        self,
        tool_specs: list[tuple[str, str, dict[str, Any]]] | None = None,
        responses: dict[str, Any] | None = None,
        servers: list[FakeServerStatus] | None = None,
    ) -> None:
        self.tool_specs = tool_specs if tool_specs is not None else [
            ("jira__search_issues", "Search Jira issues", {"type": "object", "properties": {}}),
            ("jira__create_issue", "Create a Jira issue", {"type": "object", "properties": {}}),
        ]
        self.responses = responses or {}
        self.servers = servers if servers is not None else [
            FakeServerStatus(name="jira", healthy=True, tool_count=len(self.tool_specs))
        ]
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def status(self) -> list[FakeServerStatus]:
        return list(self.servers)

    def list_all_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": desc,
                    "parameters": params,
                },
            }
            for (name, desc, params) in self.tool_specs
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, dict(arguments)))
        entry = self.responses.get(name)
        if entry is None:
            return {"content": f"ok:{name}", "is_error": False, "structured": None}
        if callable(entry):
            value = entry(arguments)
            if asyncio.iscoroutine(value):
                value = await value
            return value
        return entry


# --------- DB reset between tests ---------

@pytest_asyncio.fixture
async def db_initialized():
    """Create (or reset) tables in the in-memory SQLite DB for each test."""
    from db.session import async_session_factory, engine, init_db
    from sqlmodel import SQLModel

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
    await init_db()

    yield async_session_factory

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)


# --------- factory fixtures ---------

@pytest.fixture
def fake_llm_factory():
    def _make(script: list[list[FakeChunk]] | None = None) -> FakeOpenRouter:
        return FakeOpenRouter(script=script)

    return _make


@pytest.fixture
def fake_mcp_factory():
    def _make(**kwargs: Any) -> FakeMCPManager:
        return FakeMCPManager(**kwargs)

    return _make
