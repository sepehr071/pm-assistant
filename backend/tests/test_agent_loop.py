from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tests.conftest import (
    FakeMCPManager,
    FakeOpenRouter,
    stop_chunk,
    token_chunk,
    tool_call_chunk,
)


async def _seed_conversation(db_factory) -> int:
    from db.models import Conversation

    async with db_factory() as session:
        convo = Conversation(title="Test Chat")
        session.add(convo)
        await session.commit()
        await session.refresh(convo)
        return convo.id


async def _collect(agent_session, text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    async for evt in agent_session.run_turn(text):
        events.append(evt)
    return events


def _fake_settings() -> Any:
    class _S:
        openrouter_default_model = "anthropic/claude-sonnet-4.6"

    return _S()


@pytest.mark.asyncio
async def test_turn_no_tool_calls_yields_tokens_and_done(
    db_initialized, fake_llm_factory, fake_mcp_factory
):
    from agent.loop import AgentSession

    db_factory = db_initialized
    chat_id = await _seed_conversation(db_factory)

    llm = fake_llm_factory(
        script=[
            [
                token_chunk("Hello"),
                token_chunk(", world"),
                stop_chunk("stop"),
            ]
        ]
    )
    mcp = fake_mcp_factory()

    session = AgentSession(
        chat_id=chat_id,
        llm=llm,
        mcp=mcp,
        settings=_fake_settings(),
        auto_approve=set(),
        db_factory=db_factory,
        default_model="anthropic/claude-sonnet-4.6",
    )

    events = await _collect(session, "hi there")

    types = [e["type"] for e in events]
    assert types == ["token", "token", "done"]
    assert events[0]["delta"] == "Hello"
    assert events[1]["delta"] == ", world"
    assert events[2]["message_id"] > 0

    # persisted user + assistant message
    from db.models import Message
    from sqlalchemy import select

    async with db_factory() as s:
        rows = (
            await s.execute(
                select(Message).where(Message.conversation_id == chat_id).order_by(Message.id)
            )
        ).scalars().all()
    assert [r.role for r in rows] == ["user", "assistant"]
    assert rows[0].content == "hi there"
    assert rows[1].content == "Hello, world"


@pytest.mark.asyncio
async def test_turn_with_read_tool_auto_runs(
    db_initialized, fake_llm_factory, fake_mcp_factory
):
    from agent.loop import AgentSession

    db_factory = db_initialized
    chat_id = await _seed_conversation(db_factory)

    llm = fake_llm_factory(
        script=[
            # turn 1 — assistant emits a read tool call
            [
                tool_call_chunk(
                    index=0,
                    id="call_1",
                    name="jira__search_issues",
                    args_piece='{"jql":"assignee=me"}',
                ),
                stop_chunk("tool_calls"),
            ],
            # turn 2 — assistant follows up with text
            [
                token_chunk("Found 3 issues."),
                stop_chunk("stop"),
            ],
        ]
    )
    mcp = fake_mcp_factory(
        responses={
            "jira__search_issues": {
                "content": "3 issues",
                "is_error": False,
                "structured": None,
            }
        }
    )

    session = AgentSession(
        chat_id=chat_id,
        llm=llm,
        mcp=mcp,
        settings=_fake_settings(),
        auto_approve=set(),
        db_factory=db_factory,
        default_model="anthropic/claude-sonnet-4.6",
    )

    events = await _collect(session, "find my tickets")
    types = [e["type"] for e in events]
    # READ tools skip the approval step — no tool_call_request event
    assert "tool_call_request" not in types
    assert types == ["tool_call_start", "tool_call_result", "token", "done"]

    start_evt = events[0]
    assert start_evt["tool_call_id"] == "call_1"
    assert start_evt["tool_name"] == "jira__search_issues"
    assert start_evt["arguments"] == {"jql": "assignee=me"}

    result_evt = events[1]
    assert result_evt["tool_call_id"] == "call_1"
    assert result_evt["tool_name"] == "jira__search_issues"
    assert result_evt["result"] == "3 issues"
    assert result_evt["is_error"] is False

    assert mcp.calls == [("jira__search_issues", {"jql": "assignee=me"})]


@pytest.mark.asyncio
async def test_turn_with_write_tool_requires_approval(
    db_initialized, fake_llm_factory, fake_mcp_factory
):
    from agent.loop import AgentSession

    db_factory = db_initialized
    chat_id = await _seed_conversation(db_factory)

    llm = fake_llm_factory(
        script=[
            [
                tool_call_chunk(
                    index=0,
                    id="call_w1",
                    name="jira__create_issue",
                    args_piece='{"summary":"test"}',
                ),
                stop_chunk("tool_calls"),
            ],
            [
                token_chunk("Created ticket."),
                stop_chunk("stop"),
            ],
        ]
    )
    mcp = fake_mcp_factory(
        responses={
            "jira__create_issue": {
                "content": "PROJ-1",
                "is_error": False,
                "structured": None,
            }
        }
    )

    session = AgentSession(
        chat_id=chat_id,
        llm=llm,
        mcp=mcp,
        settings=_fake_settings(),
        auto_approve=set(),
        db_factory=db_factory,
        default_model="anthropic/claude-sonnet-4.6",
    )

    events: list[dict[str, Any]] = []

    async def drive():
        async for evt in session.run_turn("create a ticket titled test"):
            events.append(evt)

    task = asyncio.create_task(drive())

    # Wait for the tool_call_request event to arrive.
    deadline = asyncio.get_event_loop().time() + 2.0
    while not any(e["type"] == "tool_call_request" for e in events):
        await asyncio.sleep(0.01)
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError("timed out waiting for tool_call_request")

    req = next(e for e in events if e["type"] == "tool_call_request")
    assert req["tool_call_id"] == "call_w1"
    assert req["tool_name"] == "jira__create_issue"
    assert req["arguments"] == {"summary": "test"}

    assert session.approve("call_w1", True) is True

    await asyncio.wait_for(task, timeout=5.0)

    types = [e["type"] for e in events]
    assert types == ["tool_call_request", "tool_call_result", "token", "done"]

    result_evt = next(e for e in events if e["type"] == "tool_call_result")
    assert result_evt["result"] == "PROJ-1"
    assert result_evt["is_error"] is False

    assert mcp.calls == [("jira__create_issue", {"summary": "test"})]


@pytest.mark.asyncio
async def test_turn_with_write_tool_rejected(
    db_initialized, fake_llm_factory, fake_mcp_factory
):
    from agent.loop import AgentSession

    db_factory = db_initialized
    chat_id = await _seed_conversation(db_factory)

    llm = fake_llm_factory(
        script=[
            [
                tool_call_chunk(
                    index=0,
                    id="call_r1",
                    name="jira__create_issue",
                    args_piece='{"summary":"nope"}',
                ),
                stop_chunk("tool_calls"),
            ],
            [
                token_chunk("Ok, I won't create it."),
                stop_chunk("stop"),
            ],
        ]
    )
    mcp = fake_mcp_factory()

    session = AgentSession(
        chat_id=chat_id,
        llm=llm,
        mcp=mcp,
        settings=_fake_settings(),
        auto_approve=set(),
        db_factory=db_factory,
        default_model="anthropic/claude-sonnet-4.6",
    )

    events: list[dict[str, Any]] = []

    async def drive():
        async for evt in session.run_turn("please don't"):
            events.append(evt)

    task = asyncio.create_task(drive())

    deadline = asyncio.get_event_loop().time() + 2.0
    while not any(e["type"] == "tool_call_request" for e in events):
        await asyncio.sleep(0.01)
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError("timed out waiting for tool_call_request")

    assert session.approve("call_r1", False) is True

    await asyncio.wait_for(task, timeout=5.0)

    types = [e["type"] for e in events]
    assert types == ["tool_call_request", "tool_call_result", "token", "done"]

    result_evt = next(e for e in events if e["type"] == "tool_call_result")
    assert result_evt["is_error"] is True
    assert "reject" in result_evt["result"].lower()

    # the tool was NOT called on the MCP manager
    assert mcp.calls == []


@pytest.mark.asyncio
async def test_auto_approve_bypasses_gate(
    db_initialized, fake_llm_factory, fake_mcp_factory
):
    from agent.loop import AgentSession

    db_factory = db_initialized
    chat_id = await _seed_conversation(db_factory)

    llm = fake_llm_factory(
        script=[
            [
                tool_call_chunk(
                    index=0,
                    id="call_auto",
                    name="jira__create_issue",
                    args_piece='{"summary":"auto"}',
                ),
                stop_chunk("tool_calls"),
            ],
            [
                token_chunk("done"),
                stop_chunk("stop"),
            ],
        ]
    )
    mcp = fake_mcp_factory(
        responses={
            "jira__create_issue": {
                "content": "PROJ-42",
                "is_error": False,
                "structured": None,
            }
        }
    )

    session = AgentSession(
        chat_id=chat_id,
        llm=llm,
        mcp=mcp,
        settings=_fake_settings(),
        auto_approve={"jira__create_issue"},
        db_factory=db_factory,
        default_model="anthropic/claude-sonnet-4.6",
    )

    events = await _collect(session, "create it")
    types = [e["type"] for e in events]
    # auto-approved — no request event
    assert "tool_call_request" not in types
    assert types == ["tool_call_start", "tool_call_result", "token", "done"]


# ---------------------------------------------------------------------------
# History cutoff, cap, prompt rebuild, reset — protect against the
# "LLM anchors on its own earlier hallucination" bug.
# ---------------------------------------------------------------------------


async def _seed_message(db_factory, chat_id: int, role: str, content: str):
    from db.models import Message

    async with db_factory() as session:
        msg = Message(conversation_id=chat_id, role=role, content=content)
        session.add(msg)
        await session.commit()
        await session.refresh(msg)
        return msg


def _build_session(db_factory, chat_id: int, prompt="sys", builder=None):
    from agent.loop import AgentSession

    class _FakeMcp:
        def list_all_tools(self):
            return []

        async def call_tool(self, name, args):
            return {"content": "", "is_error": False}

    return AgentSession(
        chat_id=chat_id,
        llm=object(),
        mcp=_FakeMcp(),
        settings=_fake_settings(),
        auto_approve=set(),
        db_factory=db_factory,
        default_model="m",
        system_prompt=prompt,
        system_prompt_builder=builder,
    )


@pytest.mark.asyncio
async def test_load_history_respects_cutoff(db_initialized) -> None:
    """When Conversation.history_cutoff_at is set, prior messages are
    excluded from the replayed history — only messages newer than the
    cutoff feed the LLM."""
    from datetime import UTC, datetime, timedelta

    from db.models import Conversation

    db_factory = db_initialized
    chat_id = await _seed_conversation(db_factory)

    # Seed three messages at monotonically increasing timestamps.
    m1 = await _seed_message(db_factory, chat_id, "user", "old-1")
    m2 = await _seed_message(db_factory, chat_id, "assistant", "old-2")
    m3 = await _seed_message(db_factory, chat_id, "user", "new-3")

    # Put the cutoff between m2 and m3.
    async with db_factory() as session:
        convo = await session.get(Conversation, chat_id)
        assert convo is not None
        convo.history_cutoff_at = m2.created_at + timedelta(microseconds=1)
        session.add(convo)
        await session.commit()

    session = _build_session(db_factory, chat_id)
    messages = await session._load_history()

    texts = [m.get("content") for m in messages if m.get("role") in ("user", "assistant")]
    assert "old-1" not in texts
    assert "old-2" not in texts
    assert "new-3" in texts


@pytest.mark.asyncio
async def test_load_history_caps_at_max(db_initialized) -> None:
    """Very long histories are tail-truncated to MAX_HISTORY_MESSAGES."""
    from agent.loop import MAX_HISTORY_MESSAGES

    db_factory = db_initialized
    chat_id = await _seed_conversation(db_factory)

    for i in range(MAX_HISTORY_MESSAGES + 20):
        await _seed_message(db_factory, chat_id, "user", f"m-{i}")

    session = _build_session(db_factory, chat_id)
    messages = await session._load_history()

    # Exclude the system prompt from the count.
    non_system = [m for m in messages if m.get("role") != "system"]
    assert len(non_system) == MAX_HISTORY_MESSAGES
    # Tail: last message replayed should be the most recent we seeded.
    assert non_system[-1]["content"] == f"m-{MAX_HISTORY_MESSAGES + 19}"


@pytest.mark.asyncio
async def test_system_prompt_rebuilt_per_turn(db_initialized) -> None:
    """The system prompt builder callback runs on every _load_history
    call, so a session picks up integration/settings changes mid-life."""
    db_factory = db_initialized
    chat_id = await _seed_conversation(db_factory)

    calls = {"n": 0}
    prompts = ["first", "second", "third"]

    async def _builder() -> str:
        calls["n"] += 1
        idx = min(calls["n"] - 1, len(prompts) - 1)
        return prompts[idx]

    session = _build_session(db_factory, chat_id, prompt=None, builder=_builder)

    first = await session._load_history()
    second = await session._load_history()
    third = await session._load_history()

    assert calls["n"] == 3
    assert first[0]["content"][0]["text"] == "first"
    assert second[0]["content"][0]["text"] == "second"
    assert third[0]["content"][0]["text"] == "third"


@pytest.mark.asyncio
async def test_reset_history_sets_cutoff(db_initialized) -> None:
    """AgentSession.reset_history() writes Conversation.history_cutoff_at
    and the next load returns no prior messages."""
    db_factory = db_initialized
    chat_id = await _seed_conversation(db_factory)

    await _seed_message(db_factory, chat_id, "user", "pre-reset")
    session = _build_session(db_factory, chat_id)

    # Confirm the pre-reset message is visible first.
    before = await session._load_history()
    before_texts = [m.get("content") for m in before if m.get("role") == "user"]
    assert "pre-reset" in before_texts

    cutoff = await session.reset_history()
    assert cutoff is not None

    # After reset, no prior messages leak into the history.
    after = await session._load_history()
    after_texts = [m.get("content") for m in after if m.get("role") == "user"]
    assert "pre-reset" not in after_texts
    # The cutoff was persisted.
    from db.models import Conversation

    async with db_factory() as s:
        convo = await s.get(Conversation, chat_id)
        assert convo is not None
        assert convo.history_cutoff_at is not None
