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
    usage_chunk,
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
    # One accumulated usage event lands immediately before done (contract #1).
    assert types == ["token", "token", "usage", "done"]
    assert events[0]["delta"] == "Hello"
    assert events[1]["delta"] == ", world"
    assert events[-1]["message_id"] > 0

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
    assert types == [
        "tool_call_start",
        "tool_call_result",
        "token",
        "usage",
        "done",
    ]

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
    assert types == ["tool_call_request", "tool_call_result", "token", "usage", "done"]

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
    assert types == ["tool_call_request", "tool_call_result", "token", "usage", "done"]

    result_evt = next(e for e in events if e["type"] == "tool_call_result")
    assert result_evt["is_error"] is True
    assert "reject" in result_evt["result"].lower()

    # the tool was NOT called on the MCP manager
    assert mcp.calls == []


@pytest.mark.asyncio
async def test_turn_with_write_tool_times_out_when_never_approved(
    db_initialized, fake_llm_factory, fake_mcp_factory
):
    """A forgotten approval must not pin the session forever.

    With ``approval_timeout`` set, an approval gate nobody answers resolves
    as a rejection: "User rejected this action." is fed back to the LLM,
    the turn continues, and a ``done`` event arrives. No hang.
    """
    from agent.loop import AgentSession

    db_factory = db_initialized
    chat_id = await _seed_conversation(db_factory)

    llm = fake_llm_factory(
        script=[
            [
                tool_call_chunk(
                    index=0,
                    id="call_to1",
                    name="jira__create_issue",
                    args_piece='{"summary":"forgotten"}',
                ),
                stop_chunk("tool_calls"),
            ],
            [
                token_chunk("No worries, I won't create it."),
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
        approval_timeout=0.05,
    )

    # No approve() is ever called — the gate must self-resolve via timeout.
    events = await asyncio.wait_for(_collect(session, "create a ticket"), timeout=5.0)

    types = [e["type"] for e in events]
    assert types == ["tool_call_request", "tool_call_result", "token", "usage", "done"]

    result_evt = next(e for e in events if e["type"] == "tool_call_result")
    assert result_evt["tool_call_id"] == "call_to1"
    assert result_evt["is_error"] is True
    assert "reject" in result_evt["result"].lower()

    # Timed-out gate must NOT dispatch the tool to the MCP manager.
    assert mcp.calls == []

    # Gate consumed — no leftover pending approval pinning the session.
    assert session.pending == {}


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
    assert types == ["tool_call_start", "tool_call_result", "token", "usage", "done"]


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


# ---------------------------------------------------------------------------
# Agent-loop robustness (audit item #4 backend half)
# ---------------------------------------------------------------------------


class _ExplodingStream:
    """Async iterator that yields a few token chunks then raises mid-stream.

    Reproduces a provider dropping the connection partway through a response
    after the user has already seen streamed text on screen.
    """

    def __init__(self, chunks: list[Any]) -> None:
        self._chunks = chunks

    def __aiter__(self) -> "_ExplodingStream":
        self._it = iter(self._chunks)
        return self

    async def __anext__(self) -> Any:
        await asyncio.sleep(0)
        try:
            return next(self._it)
        except StopIteration:
            raise RuntimeError("provider connection reset") from None


class _ExplodingLLM:
    def chat_stream(self, **kwargs: Any) -> _ExplodingStream:
        return _ExplodingStream([token_chunk("Partial "), token_chunk("answer")])


@pytest.mark.asyncio
async def test_partial_text_persisted_on_midstream_error(db_initialized) -> None:
    """(a) When the LLM stream dies mid-response, the assistant text the user
    already watched stream in must be persisted before the error event fires,
    so it survives a page reload instead of vanishing."""
    from agent.loop import AgentSession
    from db.models import Message
    from sqlalchemy import select

    db_factory = db_initialized
    chat_id = await _seed_conversation(db_factory)

    class _FakeMcp:
        def list_all_tools(self):
            return []

        async def call_tool(self, name, args):
            return {"content": "", "is_error": False}

    session = AgentSession(
        chat_id=chat_id,
        llm=_ExplodingLLM(),
        mcp=_FakeMcp(),
        settings=_fake_settings(),
        auto_approve=set(),
        db_factory=db_factory,
        default_model="m",
        system_prompt="sys",
    )

    events = await _collect(session, "ask something")

    types = [e["type"] for e in events]
    # Streamed tokens, then an error — no done.
    assert types[:2] == ["token", "token"]
    assert types[-1] == "error"

    # The streamed assistant text must be persisted (survives reload).
    async with db_factory() as s:
        rows = (
            await s.execute(
                select(Message)
                .where(Message.conversation_id == chat_id)
                .order_by(Message.id)
            )
        ).scalars().all()
    roles = [r.role for r in rows]
    assert "assistant" in roles
    assistant_rows = [r for r in rows if r.role == "assistant"]
    assert any(
        r.content and "Partial answer" in r.content for r in assistant_rows
    ), f"partial streamed text lost: {[r.content for r in assistant_rows]}"


@pytest.mark.asyncio
async def test_usage_emitted_before_midstream_error_when_accumulated(
    db_initialized,
) -> None:
    """Usage already accumulated when a later stream failure aborts the turn
    must not be silently dropped — the usage event fires before the error so
    the frontend still accounts for spent tokens."""
    from agent.loop import AgentSession

    db_factory = db_initialized
    chat_id = await _seed_conversation(db_factory)

    class _FakeMcp:
        def list_all_tools(self):
            return []

        async def call_tool(self, name, args):
            return {"content": "", "is_error": False}

    class _UsageThenExplodeLLM:
        def chat_stream(self, **kwargs: Any) -> _ExplodingStream:
            return _ExplodingStream(
                [
                    token_chunk("Partial "),
                    usage_chunk(prompt_tokens=10, completion_tokens=2, cost=0.0001),
                ]
            )

    session = AgentSession(
        chat_id=chat_id,
        llm=_UsageThenExplodeLLM(),
        mcp=_FakeMcp(),
        settings=_fake_settings(),
        auto_approve=set(),
        db_factory=db_factory,
        default_model="m",
        system_prompt="sys",
    )

    events = await _collect(session, "ask something")
    types = [e["type"] for e in events]
    assert types[-2:] == ["usage", "error"]
    usage_evt = events[-2]
    assert usage_evt["prompt_tokens"] == 10
    assert usage_evt["completion_tokens"] == 2
    assert usage_evt["cost"] == pytest.approx(0.0001)


async def _seed_tool_message(
    db_factory, chat_id: int, role: str, *, content=None, tool_calls=None,
    tool_call_id=None, name=None,
):
    import json as _json
    from db.models import Message

    async with db_factory() as session:
        msg = Message(
            conversation_id=chat_id,
            role=role,
            content=content,
            tool_calls=_json.dumps(tool_calls) if tool_calls else None,
            tool_call_id=tool_call_id,
            name=name,
        )
        session.add(msg)
        await session.commit()
        await session.refresh(msg)
        return msg


@pytest.mark.asyncio
async def test_history_slice_never_starts_with_orphan_tool_message(
    db_initialized,
) -> None:
    """(b) The MAX_HISTORY_MESSAGES tail-slice must not split an assistant
    tool_calls message from its tool-result rows. If the cut lands between
    them, the leading orphan tool messages are dropped so the replayed
    window starts at a clean user/assistant boundary — otherwise OpenRouter
    returns 400 'tool message without preceding tool_calls'."""
    from agent.loop import MAX_HISTORY_MESSAGES

    db_factory = db_initialized
    chat_id = await _seed_conversation(db_factory)

    # Construction: assistant(tool_calls) + 2 tool rows are the OLDEST
    # content, followed by (MAX - 2) filler user rows + 1 newest user row.
    # Total = MAX + 1 rows, so the tail-slice rows[-MAX:] drops exactly the
    # leading assistant(tool_calls) row and strands its two tool results at
    # the head of the kept window — the orphan-tool-message bug.
    await _seed_tool_message(
        db_factory,
        chat_id,
        "assistant",
        content=None,
        tool_calls=[
            {
                "id": "call_x",
                "type": "function",
                "function": {"name": "jira__search_issues", "arguments": "{}"},
            }
        ],
    )
    await _seed_tool_message(
        db_factory,
        chat_id,
        "tool",
        content="result-1",
        tool_call_id="call_x",
        name="jira__search_issues",
    )
    await _seed_tool_message(
        db_factory,
        chat_id,
        "tool",
        content="result-2",
        tool_call_id="call_x",
        name="jira__search_issues",
    )
    for i in range(MAX_HISTORY_MESSAGES - 2):
        await _seed_message(db_factory, chat_id, "user", f"f-{i}")
    await _seed_message(db_factory, chat_id, "user", "newest")

    session = _build_session(db_factory, chat_id)
    messages = await session._load_history()

    non_system = [m for m in messages if m.get("role") != "system"]
    # The replayed window must not begin with an orphan tool message.
    assert non_system, "expected some replayed history"
    assert non_system[0]["role"] != "tool", (
        f"history window starts with orphan tool message: {non_system[0]}"
    )

    # Invariant: every tool message must be preceded (somewhere earlier in the
    # list) by an assistant message carrying a tool_calls entry with its id.
    pending_ids: set[str] = set()
    for m in non_system:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                pending_ids.add(tc["id"])
        elif m.get("role") == "tool":
            assert m.get("tool_call_id") in pending_ids, (
                f"tool message {m.get('tool_call_id')} has no preceding "
                f"tool_calls in window"
            )


@pytest.mark.asyncio
async def test_iteration_cap_yields_truncated_done_not_error(
    db_initialized, fake_mcp_factory,
) -> None:
    """(c) Hitting the iteration cap must not look like a crash. Instead the
    loop appends a short assistant note, persists it, and yields a normal
    `done` event carrying `truncated: True` — never a bare `error`."""
    from agent.loop import AgentSession
    from db.models import Message
    from sqlalchemy import select

    db_factory = db_initialized
    chat_id = await _seed_conversation(db_factory)

    class _ForeverToolLLM:
        """Always returns the same auto-approved read tool call, so the loop
        never reaches a no-tool-call terminal state and exhausts the cap."""

        def __init__(self) -> None:
            self.calls = 0

        def chat_stream(self, **kwargs: Any):
            self.calls += 1
            n = self.calls

            async def _gen():
                yield tool_call_chunk(
                    index=0,
                    id=f"call_{n}",
                    name="jira__search_issues",
                    args_piece="{}",
                )
                yield stop_chunk("tool_calls")

            return _gen()

    mcp = fake_mcp_factory(
        responses={
            "jira__search_issues": {
                "content": "ok",
                "is_error": False,
                "structured": None,
            }
        }
    )

    session = AgentSession(
        chat_id=chat_id,
        llm=_ForeverToolLLM(),
        mcp=mcp,
        settings=_fake_settings(),
        auto_approve=set(),
        db_factory=db_factory,
        default_model="m",
        system_prompt="sys",
    )

    events = await _collect(session, "loop forever")

    types = [e["type"] for e in events]
    # No crash-looking error event.
    assert "error" not in types
    # Terminates with a done event flagged truncated.
    assert types[-1] == "done"
    done_evt = events[-1]
    assert done_evt.get("truncated") is True
    assert done_evt.get("message_id", 0) > 0

    # A short assistant note was persisted so the UI/audit trail explains
    # the stop instead of leaving a dangling tool turn.
    async with db_factory() as s:
        rows = (
            await s.execute(
                select(Message)
                .where(Message.conversation_id == chat_id)
                .where(Message.role == "assistant")
                .order_by(Message.id)
            )
        ).scalars().all()
    assert rows, "expected an assistant message"
    last = rows[-1]
    assert last.content, "truncation note must carry text"
    assert "continue" in last.content.lower()


# ---------------------------------------------------------------------------
# Usage accounting (audit item #5 backend half) — single `usage` SSE event,
# accumulated across every loop iteration, emitted right before `done`.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_usage_event_emitted_before_done_single_iteration(
    db_initialized, fake_llm_factory, fake_mcp_factory
) -> None:
    """A turn with no tool calls emits exactly one `usage` event carrying the
    final chunk's token counts + cost, immediately before `done`."""
    from agent.loop import AgentSession

    db_factory = db_initialized
    chat_id = await _seed_conversation(db_factory)

    llm = fake_llm_factory(
        script=[
            [
                token_chunk("Hello"),
                stop_chunk("stop"),
                usage_chunk(
                    prompt_tokens=12,
                    completion_tokens=5,
                    total_tokens=17,
                    cost=0.00042,
                ),
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

    events = await _collect(session, "hi")
    types = [e["type"] for e in events]

    assert "usage" in types
    # Exactly one usage event, and it sits immediately before done.
    assert types.count("usage") == 1
    assert types[-2:] == ["usage", "done"]

    usage_evt = next(e for e in events if e["type"] == "usage")
    assert usage_evt["prompt_tokens"] == 12
    assert usage_evt["completion_tokens"] == 5
    assert usage_evt["total_tokens"] == 17
    assert usage_evt["cost"] == pytest.approx(0.00042)


@pytest.mark.asyncio
async def test_usage_accumulates_across_iterations(
    db_initialized, fake_llm_factory, fake_mcp_factory
) -> None:
    """Each LLM call in a multi-iteration turn yields its own usage chunk; the
    loop sums them into a single `usage` event emitted once, before `done`."""
    from agent.loop import AgentSession

    db_factory = db_initialized
    chat_id = await _seed_conversation(db_factory)

    llm = fake_llm_factory(
        script=[
            # iteration 1 — tool call + its own usage chunk
            [
                tool_call_chunk(
                    index=0,
                    id="call_1",
                    name="jira__search_issues",
                    args_piece="{}",
                ),
                stop_chunk("tool_calls"),
                usage_chunk(
                    prompt_tokens=100,
                    completion_tokens=20,
                    total_tokens=120,
                    cost=0.001,
                ),
            ],
            # iteration 2 — final text + its own usage chunk
            [
                token_chunk("Done."),
                stop_chunk("stop"),
                usage_chunk(
                    prompt_tokens=150,
                    completion_tokens=10,
                    total_tokens=160,
                    cost=0.002,
                ),
            ],
        ]
    )
    mcp = fake_mcp_factory(
        responses={
            "jira__search_issues": {
                "content": "ok",
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

    events = await _collect(session, "search then summarise")
    types = [e["type"] for e in events]

    # One accumulated usage event, immediately before done.
    assert types.count("usage") == 1
    assert types[-2:] == ["usage", "done"]

    usage_evt = next(e for e in events if e["type"] == "usage")
    assert usage_evt["prompt_tokens"] == 250
    assert usage_evt["completion_tokens"] == 30
    assert usage_evt["total_tokens"] == 280
    assert usage_evt["cost"] == pytest.approx(0.003)


@pytest.mark.asyncio
async def test_usage_event_cost_null_when_absent(
    db_initialized, fake_llm_factory, fake_mcp_factory
) -> None:
    """When no chunk carried a cost, the usage event's `cost` is null."""
    from agent.loop import AgentSession

    db_factory = db_initialized
    chat_id = await _seed_conversation(db_factory)

    llm = fake_llm_factory(
        script=[
            [
                token_chunk("Hi"),
                stop_chunk("stop"),
                usage_chunk(prompt_tokens=8, completion_tokens=3, cost=None),
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

    events = await _collect(session, "hi")
    usage_evt = next(e for e in events if e["type"] == "usage")
    assert usage_evt["prompt_tokens"] == 8
    assert usage_evt["completion_tokens"] == 3
    assert usage_evt["total_tokens"] == 11
    assert usage_evt["cost"] is None


@pytest.mark.asyncio
async def test_usage_event_emitted_even_without_usage_chunk(
    db_initialized, fake_llm_factory, fake_mcp_factory
) -> None:
    """A provider that never sends a usage chunk still gets a single usage
    event (all zeros, cost null) so the frontend contract is always honoured."""
    from agent.loop import AgentSession

    db_factory = db_initialized
    chat_id = await _seed_conversation(db_factory)

    llm = fake_llm_factory(
        script=[[token_chunk("Hello"), stop_chunk("stop")]]
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

    events = await _collect(session, "hi")
    types = [e["type"] for e in events]
    assert types.count("usage") == 1
    assert types[-2:] == ["usage", "done"]
    usage_evt = next(e for e in events if e["type"] == "usage")
    assert usage_evt["prompt_tokens"] == 0
    assert usage_evt["completion_tokens"] == 0
    assert usage_evt["total_tokens"] == 0
    assert usage_evt["cost"] is None


@pytest.mark.asyncio
async def test_usage_event_before_truncated_done(
    db_initialized, fake_mcp_factory
) -> None:
    """The iteration-cap exit path also emits one accumulated usage event
    before the truncated `done`."""
    from agent.loop import AgentSession

    db_factory = db_initialized
    chat_id = await _seed_conversation(db_factory)

    class _ForeverToolLLM:
        def __init__(self) -> None:
            self.calls = 0

        def chat_stream(self, **kwargs: Any):
            self.calls += 1
            n = self.calls

            async def _gen():
                yield tool_call_chunk(
                    index=0,
                    id=f"call_{n}",
                    name="jira__search_issues",
                    args_piece="{}",
                )
                yield stop_chunk("tool_calls")
                yield usage_chunk(
                    prompt_tokens=10, completion_tokens=2, cost=0.0001
                )

            return _gen()

    mcp = fake_mcp_factory(
        responses={
            "jira__search_issues": {
                "content": "ok",
                "is_error": False,
                "structured": None,
            }
        }
    )

    session = AgentSession(
        chat_id=chat_id,
        llm=_ForeverToolLLM(),
        mcp=mcp,
        settings=_fake_settings(),
        auto_approve=set(),
        db_factory=db_factory,
        default_model="m",
        system_prompt="sys",
    )

    events = await _collect(session, "loop forever")
    types = [e["type"] for e in events]
    assert "error" not in types
    assert types.count("usage") == 1
    assert types[-2:] == ["usage", "done"]
    assert events[-1].get("truncated") is True

    usage_evt = next(e for e in events if e["type"] == "usage")
    # 20 iterations * 10 prompt tokens each.
    assert usage_evt["prompt_tokens"] == 200
    assert usage_evt["completion_tokens"] == 40
    assert usage_evt["cost"] == pytest.approx(0.002)


@pytest.mark.asyncio
async def test_chat_stream_opts_into_usage_and_fallback_kwargs(
    db_initialized, fake_llm_factory, fake_mcp_factory
) -> None:
    """The loop must (a) opt into streamed usage on every LLM call and (b)
    when fallback models are configured, pass [primary, *fallbacks] so a
    provider 5xx fails over."""
    from agent.loop import AgentSession

    db_factory = db_initialized
    chat_id = await _seed_conversation(db_factory)

    llm = fake_llm_factory(
        script=[[token_chunk("hi"), stop_chunk("stop")]]
    )
    mcp = fake_mcp_factory()

    class _S:
        openrouter_default_model = "primary/model"
        openrouter_fallback_models = ("fb/one", "fb/two")

    session = AgentSession(
        chat_id=chat_id,
        llm=llm,
        mcp=mcp,
        settings=_S(),
        auto_approve=set(),
        db_factory=db_factory,
        default_model="primary/model",
    )

    await _collect(session, "hi")

    assert llm.calls, "expected at least one LLM call"
    call = llm.calls[0]
    kwargs = call["kwargs"]

    # (a) usage opt-in on the streamed call.
    assert kwargs.get("stream_options") == {"include_usage": True}
    extra_body = kwargs.get("extra_body") or {}
    assert extra_body.get("usage") == {"include": True}

    # (b) fallback models threaded into extra_body["models"], primary first.
    assert extra_body.get("models") == ["primary/model", "fb/one", "fb/two"]


@pytest.mark.asyncio
async def test_chat_stream_omits_models_when_no_fallbacks(
    db_initialized, fake_llm_factory, fake_mcp_factory
) -> None:
    """With no fallback models configured, extra_body must NOT carry a
    `models` key (so OpenRouter routes the single primary model normally)."""
    from agent.loop import AgentSession

    db_factory = db_initialized
    chat_id = await _seed_conversation(db_factory)

    llm = fake_llm_factory(
        script=[[token_chunk("hi"), stop_chunk("stop")]]
    )
    mcp = fake_mcp_factory()

    session = AgentSession(
        chat_id=chat_id,
        llm=llm,
        mcp=mcp,
        settings=_fake_settings(),  # no openrouter_fallback_models attr
        auto_approve=set(),
        db_factory=db_factory,
        default_model="anthropic/claude-sonnet-4.6",
    )

    await _collect(session, "hi")

    call = llm.calls[0]
    extra_body = call["kwargs"].get("extra_body") or {}
    assert "models" not in extra_body
    # usage opt-in still present.
    assert extra_body.get("usage") == {"include": True}
    assert call["kwargs"].get("stream_options") == {"include_usage": True}
