"""Tests for the chat-driven `rules__create_rule` builtin tool.

Strategy:
- Instantiate a real `MCPManager` (no `start()` call — config file absent
  is a no-op) so we exercise the actual `register_builtin` / `list_all_tools`
  / `call_tool` wiring.
- Use a scripted `FakeLLM` to drive `compile_rule`.
- Use the shared in-memory DB via the `db_initialized` fixture from
  `conftest.py`.
- Assert the Rule row is persisted and `scheduler.reload()` is invoked.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from db.models import Rule
from mcp_manager import MCPManager
from services.rule_tool import register


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeMessage:
    content: str | None


@dataclass
class _FakeChoice:
    message: _FakeMessage


@dataclass
class _FakeCompletion:
    choices: list[_FakeChoice]


class FakeLLM:
    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> _FakeCompletion:
        self.calls.append({"model": model, "messages": list(messages)})
        if not self._replies:
            raise AssertionError("FakeLLM exhausted")
        return _FakeCompletion(choices=[_FakeChoice(message=_FakeMessage(content=self._replies.pop(0)))])


class FailingLLM:
    async def chat(self, **kwargs: Any) -> _FakeCompletion:
        return _FakeCompletion(choices=[_FakeChoice(message=_FakeMessage(content="not-valid-json"))])


@dataclass
class FakeScheduler:
    reload_calls: int = 0

    async def reload(self) -> None:
        self.reload_calls += 1


def _make_mcp() -> MCPManager:
    """Construct an MCPManager that never talks to Smithery.

    Points at a non-existent config path so `_load_config` logs and bails;
    we never call `start()` anyway, so no HTTP happens.
    """
    return MCPManager(
        config_path=Path("nonexistent_integrations.json"),
        smithery_api_key="test-key",
        smithery_namespace="test-ns",
    )


_VALID_SPEC_JSON = json.dumps(
    {
        "source_tool": "slack__conversations_history",
        "source_args": {"channel": "D0123", "limit": 20},
        "filter": {
            "kind": "message_from_user_contains",
            "user": "Sepehr",
            "contains": ["roadmap"],
        },
        "action_prompt": "Reply: I'll review the roadmap today.",
    }
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_register_exposes_rules_create_rule_tool(db_initialized) -> None:
    mcp = _make_mcp()
    llm = FakeLLM([_VALID_SPEC_JSON])
    scheduler = FakeScheduler()

    register(
        mcp,
        llm=llm,
        db_factory=db_initialized,
        scheduler=scheduler,
        default_model="test/model",
    )

    tools = mcp.list_all_tools()
    names = [t["function"]["name"] for t in tools]
    assert "rules__create_rule" in names

    spec = next(t for t in tools if t["function"]["name"] == "rules__create_rule")
    params = spec["function"]["parameters"]
    assert "description" in params["properties"]
    assert "interval_seconds" in params["properties"]
    assert "auto_approve" in params["properties"]
    assert "description" in params["required"]
    assert "interval_seconds" in params["required"]


async def test_create_rule_happy_path_persists_and_reloads(db_initialized) -> None:
    mcp = _make_mcp()
    llm = FakeLLM([_VALID_SPEC_JSON])
    scheduler = FakeScheduler()

    register(
        mcp,
        llm=llm,
        db_factory=db_initialized,
        scheduler=scheduler,
        default_model="test/model",
    )

    result = await mcp.call_tool(
        "rules__create_rule",
        {
            "description": "whenever Sepehr DMs me about roadmap, reply today",
            "interval_seconds": 300,
            "auto_approve": False,
        },
    )

    assert result["is_error"] is False
    structured = result["structured"]
    assert structured is not None
    assert structured["interval_seconds"] == 300
    assert structured["auto_approve"] is False
    assert structured["compiled_spec"]["source_tool"] == "slack__conversations_history"
    assert scheduler.reload_calls == 1

    async with db_initialized() as session:
        rows = (await session.execute(select(Rule))).scalars().all()
        assert len(rows) == 1
        rule = rows[0]
        assert rule.interval_seconds == 300
        assert rule.auto_approve is False
        assert rule.enabled is True
        compiled = json.loads(rule.compiled_spec)
        assert compiled["filter"]["kind"] == "message_from_user_contains"

        # A per-rule activity conversation was allocated in the same flow.
        from db.models import Conversation

        assert rule.activity_conversation_id is not None
        convo = await session.get(Conversation, rule.activity_conversation_id)
        assert convo is not None
        assert convo.kind == "rule_activity"
        assert convo.title.startswith("Rule:")


async def test_create_rule_rejects_short_interval(db_initialized) -> None:
    mcp = _make_mcp()
    llm = FakeLLM([_VALID_SPEC_JSON])
    scheduler = FakeScheduler()

    register(
        mcp,
        llm=llm,
        db_factory=db_initialized,
        scheduler=scheduler,
        default_model="test/model",
    )

    result = await mcp.call_tool(
        "rules__create_rule",
        {
            "description": "something",
            "interval_seconds": 10,
            "auto_approve": False,
        },
    )

    assert result["is_error"] is True
    assert "interval_seconds" in result["content"]
    assert scheduler.reload_calls == 0
    # no LLM call and no DB row
    assert llm.calls == []
    async with db_initialized() as session:
        rows = (await session.execute(select(Rule))).scalars().all()
        assert rows == []


async def test_create_rule_surfaces_compiler_failure(db_initialized) -> None:
    mcp = _make_mcp()
    # Two invalid JSON replies — compile_rule retries once, then raises.
    llm = FakeLLM(["not-json-1", "not-json-2"])
    scheduler = FakeScheduler()

    register(
        mcp,
        llm=llm,
        db_factory=db_initialized,
        scheduler=scheduler,
        default_model="test/model",
    )

    result = await mcp.call_tool(
        "rules__create_rule",
        {
            "description": "something valid",
            "interval_seconds": 300,
        },
    )

    assert result["is_error"] is True
    assert "compilation" in result["content"].lower()
    assert scheduler.reload_calls == 0
    async with db_initialized() as session:
        rows = (await session.execute(select(Rule))).scalars().all()
        assert rows == []


async def test_create_rule_rejects_empty_description(db_initialized) -> None:
    mcp = _make_mcp()
    llm = FakeLLM([])
    scheduler = FakeScheduler()

    register(
        mcp,
        llm=llm,
        db_factory=db_initialized,
        scheduler=scheduler,
        default_model="test/model",
    )

    result = await mcp.call_tool(
        "rules__create_rule",
        {"description": "   ", "interval_seconds": 300},
    )

    assert result["is_error"] is True
    assert "description" in result["content"].lower()
    assert llm.calls == []


async def test_create_rule_works_without_scheduler(db_initialized) -> None:
    mcp = _make_mcp()
    llm = FakeLLM([_VALID_SPEC_JSON])

    register(
        mcp,
        llm=llm,
        db_factory=db_initialized,
        scheduler=None,
        default_model="test/model",
    )

    result = await mcp.call_tool(
        "rules__create_rule",
        {
            "description": "test rule",
            "interval_seconds": 60,
        },
    )

    assert result["is_error"] is False
    async with db_initialized() as session:
        rows = (await session.execute(select(Rule))).scalars().all()
        assert len(rows) == 1
