from __future__ import annotations

import json
from typing import Any

import pytest

from tests.conftest import (
    FakeMCPManager,
    FakeOpenRouter,
    stop_chunk,
    token_chunk,
    tool_call_chunk,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_rule_with_activity_chat(
    db_factory,
    *,
    description: str,
    spec: dict[str, Any],
    auto_approve: bool = False,
    interval_seconds: int = 60,
    enabled: bool = True,
    state_cursor: str | None = None,
) -> tuple[int, int]:
    """Create a `Rule` and its bound per-rule activity `Conversation`.

    Returns ``(rule_id, activity_conversation_id)``.
    """
    from db.models import Conversation, Rule

    async with db_factory() as session:
        rule = Rule(
            description=description,
            compiled_spec=json.dumps(spec),
            interval_seconds=interval_seconds,
            auto_approve=auto_approve,
            enabled=enabled,
            state_cursor=state_cursor,
        )
        session.add(rule)
        await session.commit()
        await session.refresh(rule)

        convo = Conversation(
            title=f"Rule: {description[:60]}",
            kind="rule_activity",
        )
        session.add(convo)
        await session.commit()
        await session.refresh(convo)

        rule.activity_conversation_id = convo.id
        session.add(rule)
        await session.commit()
        await session.refresh(rule)
        return rule.id, convo.id  # type: ignore[return-value]


def _fake_settings() -> Any:
    class _S:
        openrouter_default_model = "anthropic/claude-sonnet-4.6"

    return _S()


def _slack_spec(*, user: str = "Sepehr", contains: list[str] | None = None) -> dict[str, Any]:
    return {
        "source_tool": "slack__conversations_history",
        "source_args": {"channel": "D0123"},
        "filter": {
            "kind": "message_from_user_contains",
            "user": user,
            "contains": contains or ["ping"],
        },
        "action_prompt": "Reply: pong.",
    }


class RaisingMCP(FakeMCPManager):
    """MCP double whose call_tool always returns an is_error result."""

    def __init__(self, error_text: str = "boom") -> None:
        super().__init__()
        self._error_text = error_text

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, dict(arguments)))
        return {"content": self._error_text, "is_error": True, "structured": None}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_sessions_and_counters():
    from api.stream import _reset_sessions
    from services import rule_engine

    _reset_sessions()
    rule_engine.reset_error_counters()
    yield
    _reset_sessions()
    rule_engine.reset_error_counters()


async def test_no_match_returns_no_match_firing_without_activity_message(
    db_initialized, fake_llm_factory, fake_mcp_factory
):
    from db.models import Message, RuleFiring
    from services import rule_engine
    from sqlalchemy import select

    db_factory = db_initialized
    rule_id, activity_id = await _seed_rule_with_activity_chat(
        db_factory,
        description="when Sepehr says ping, pong",
        spec=_slack_spec(user="Sepehr", contains=["ping"]),
    )

    # Slack returns messages none of which match (different user, different text).
    slack_payload = {
        "messages": [
            {"user": "Other", "text": "ping me later", "ts": "1700000001.0"},
            {"user": "Sepehr", "text": "good morning", "ts": "1700000002.0"},
        ]
    }
    mcp = fake_mcp_factory(
        responses={
            "slack__conversations_history": {
                "content": "",
                "is_error": False,
                "structured": slack_payload,
            }
        }
    )
    llm = fake_llm_factory()

    firing = await rule_engine.tick(
        rule_id,
        mcp=mcp,
        llm=llm,
        settings=_fake_settings(),
        db_factory=db_factory,
        default_model="anthropic/claude-sonnet-4.6",
    )

    assert firing.status == "no_match"
    # no LLM turn happened
    assert llm.calls == []

    # no messages written to the activity chat
    async with db_factory() as s:
        messages = (
            await s.execute(select(Message).where(Message.conversation_id == activity_id))
        ).scalars().all()
        assert messages == []
        # no_match firings aren't persisted
        firings = (await s.execute(select(RuleFiring))).scalars().all()
        assert firings == []


async def test_match_with_auto_approve_runs_turn_and_records_matched(
    db_initialized, fake_llm_factory, fake_mcp_factory
):
    from db.models import Message, Rule, RuleFiring
    from services import rule_engine
    from sqlalchemy import select

    db_factory = db_initialized
    rule_id, activity_id = await _seed_rule_with_activity_chat(
        db_factory,
        description="ping→pong",
        spec=_slack_spec(user="Sepehr", contains=["ping"]),
        auto_approve=True,
        state_cursor="1700000000.0",
    )

    slack_payload = {
        "messages": [
            {"user": "Sepehr", "text": "ping!", "ts": "1700000005.0"},
        ]
    }
    mcp = fake_mcp_factory(
        responses={
            "slack__conversations_history": {
                "content": "",
                "is_error": False,
                "structured": slack_payload,
            }
        }
    )
    # LLM immediately finishes (single stream, no tool calls).
    llm = fake_llm_factory(
        script=[
            [token_chunk("Replied pong."), stop_chunk("stop")],
        ]
    )

    firing = await rule_engine.tick(
        rule_id,
        mcp=mcp,
        llm=llm,
        settings=_fake_settings(),
        db_factory=db_factory,
        default_model="anthropic/claude-sonnet-4.6",
    )

    assert firing.status == "matched"
    assert firing.message_id is not None
    assert "Sepehr" in (firing.match_summary or "")

    # cursor advanced
    async with db_factory() as s:
        rule = await s.get(Rule, rule_id)
        assert rule is not None
        assert rule.state_cursor == "1700000005.0"
        assert rule.last_error is None
        assert rule.last_run_at is not None

        messages = (
            await s.execute(
                select(Message).where(Message.conversation_id == activity_id).order_by(Message.id)
            )
        ).scalars().all()
        roles = [m.role for m in messages]
        assert roles == ["user", "assistant"]
        assert "ping→pong" in (messages[0].content or "") or "[rule:" in (messages[0].content or "")

        firings = (await s.execute(select(RuleFiring))).scalars().all()
        assert len(firings) == 1
        assert firings[0].status == "matched"

    # session dropped after clean 'done'
    from api.stream import _sessions

    assert activity_id not in _sessions


async def test_match_without_auto_approve_yields_pending_approval(
    db_initialized, fake_llm_factory, fake_mcp_factory
):
    import asyncio

    from db.models import RuleFiring
    from services import rule_engine
    from sqlalchemy import select

    db_factory = db_initialized
    rule_id, activity_id = await _seed_rule_with_activity_chat(
        db_factory,
        description="create ticket on mention",
        spec={
            "source_tool": "slack__conversations_history",
            "source_args": {"channel": "D0123"},
            "filter": {
                "kind": "message_from_user_contains",
                "user": "Sepehr",
                "contains": ["bug"],
            },
            "action_prompt": "Create a jira ticket summarizing the bug.",
        },
        auto_approve=False,
    )

    slack_payload = {
        "messages": [
            {"user": "Sepehr", "text": "found a bug in billing", "ts": "1700000100.0"},
        ]
    }
    mcp = fake_mcp_factory(
        tool_specs=[
            ("slack__conversations_history", "", {"type": "object", "properties": {}}),
            ("jira__create_issue", "", {"type": "object", "properties": {}}),
        ],
        responses={
            "slack__conversations_history": {
                "content": "",
                "is_error": False,
                "structured": slack_payload,
            },
            "jira__create_issue": {
                "content": "PROJ-9",
                "is_error": False,
                "structured": None,
            },
        },
    )
    # LLM issues a write tool call that needs approval.
    llm = fake_llm_factory(
        script=[
            [
                tool_call_chunk(
                    index=0,
                    id="call_pending",
                    name="jira__create_issue",
                    args_piece='{"summary":"bug in billing"}',
                ),
                stop_chunk("tool_calls"),
            ],
        ]
    )

    firing = await asyncio.wait_for(
        rule_engine.tick(
            rule_id,
            mcp=mcp,
            llm=llm,
            settings=_fake_settings(),
            db_factory=db_factory,
            default_model="anthropic/claude-sonnet-4.6",
        ),
        timeout=2.0,
    )

    assert firing.status == "pending_approval"

    # Session remains registered so POST /approve can find it.
    from api.stream import _sessions

    assert activity_id in _sessions
    session = _sessions[activity_id]
    assert "call_pending" in session.pending

    # Persisted firing row exists
    async with db_factory() as s:
        firings = (await s.execute(select(RuleFiring))).scalars().all()
        assert len(firings) == 1
        assert firings[0].status == "pending_approval"

    # Clean up pending so the turn coroutine can finish if still parked.
    session.cancel_pending()


async def test_tool_error_records_error_and_increments_counter(
    db_initialized, fake_llm_factory
):
    from db.models import Rule, RuleFiring
    from services import rule_engine
    from sqlalchemy import select

    db_factory = db_initialized
    rule_id, _ = await _seed_rule_with_activity_chat(
        db_factory,
        description="ping→pong",
        spec=_slack_spec(),
    )

    mcp = RaisingMCP(error_text="slack auth_required")
    llm = fake_llm_factory()

    firing = await rule_engine.tick(
        rule_id,
        mcp=mcp,
        llm=llm,
        settings=_fake_settings(),
        db_factory=db_factory,
        default_model="anthropic/claude-sonnet-4.6",
    )

    assert firing.status == "error"
    assert "slack auth_required" in (firing.error or "")

    async with db_factory() as s:
        rule = await s.get(Rule, rule_id)
        assert rule is not None
        assert "slack auth_required" in (rule.last_error or "")
        assert rule.enabled is True  # one error, not yet auto-disabled

        firings = (await s.execute(select(RuleFiring))).scalars().all()
        assert len(firings) == 1
        assert firings[0].status == "error"

    # Counter is per-rule
    assert rule_engine._consecutive_errors[rule_id] == 1  # type: ignore[attr-defined]


async def test_auto_disable_after_five_consecutive_errors(
    db_initialized, fake_llm_factory
):
    from db.models import Rule
    from services import rule_engine

    db_factory = db_initialized
    rule_id, _ = await _seed_rule_with_activity_chat(
        db_factory,
        description="always errors",
        spec=_slack_spec(),
    )

    mcp = RaisingMCP(error_text="boom")
    llm = fake_llm_factory()

    for _ in range(5):
        await rule_engine.tick(
            rule_id,
            mcp=mcp,
            llm=llm,
            settings=_fake_settings(),
            db_factory=db_factory,
            default_model="anthropic/claude-sonnet-4.6",
        )

    async with db_factory() as s:
        rule = await s.get(Rule, rule_id)
        assert rule is not None
        assert rule.enabled is False
        assert "boom" in (rule.last_error or "")

    assert rule_engine._consecutive_errors[rule_id] >= 5  # type: ignore[attr-defined]


async def test_schedule_only_filter_always_matches(
    db_initialized, fake_llm_factory, fake_mcp_factory
):
    from db.models import RuleFiring
    from services import rule_engine
    from sqlalchemy import select

    db_factory = db_initialized
    rule_id, _ = await _seed_rule_with_activity_chat(
        db_factory,
        description="weekly status",
        spec={
            "source_tool": "slack__conversations_history",
            "source_args": {"channel": "C_ENG"},
            "filter": {
                "kind": "schedule_only",
                "schedule": "every Monday 09:00",
            },
            "action_prompt": "Post a weekly status summary.",
        },
        auto_approve=True,
    )

    mcp = fake_mcp_factory(
        responses={
            "slack__conversations_history": {
                "content": "",
                "is_error": False,
                "structured": {"messages": []},
            }
        }
    )
    llm = fake_llm_factory(
        script=[[token_chunk("Posted status."), stop_chunk("stop")]]
    )

    firing = await rule_engine.tick(
        rule_id,
        mcp=mcp,
        llm=llm,
        settings=_fake_settings(),
        db_factory=db_factory,
        default_model="anthropic/claude-sonnet-4.6",
    )

    assert firing.status == "matched"

    async with db_factory() as s:
        firings = (await s.execute(select(RuleFiring))).scalars().all()
        assert len(firings) == 1
        assert firings[0].status == "matched"


# ---------------------------------------------------------------------------
# _extract_error_text
# ---------------------------------------------------------------------------


def test_extract_error_text_joins_list_of_text_items() -> None:
    from services.rule_engine import _extract_error_text

    raw = {
        "is_error": True,
        "content": [
            {"type": "text", "text": "foo"},
            {"type": "text", "text": "bar"},
        ],
    }
    assert _extract_error_text(raw) == "foo\nbar"


def test_extract_error_text_plain_string() -> None:
    from services.rule_engine import _extract_error_text

    assert _extract_error_text({"content": "plain"}) == "plain"


def test_extract_error_text_none_falls_back() -> None:
    from services.rule_engine import _extract_error_text

    assert _extract_error_text({"content": None}) == "tool error"
    assert _extract_error_text({}) == "tool error"


def test_extract_error_text_empty_list_falls_back_to_str() -> None:
    from services.rule_engine import _extract_error_text

    assert _extract_error_text({"content": []}) == "[]"


def test_extract_error_text_object_with_text_attr() -> None:
    from services.rule_engine import _extract_error_text

    class _TextContent:
        def __init__(self, text: str) -> None:
            self.text = text

    raw = {"content": [_TextContent("hello")]}
    assert _extract_error_text(raw) == "hello"


# ---------------------------------------------------------------------------
# Track A — per-rule activity chats
# ---------------------------------------------------------------------------


async def test_concurrent_rules_no_longer_block_each_other(
    db_initialized, fake_llm_factory, fake_mcp_factory
):
    """Rule A parked on approval must NOT block Rule B from running.

    Pre-Track-A, both rules shared the singleton activity chat id and
    therefore the same `_sessions` slot, so a parked approval on A made B's
    tick go straight to "skipped". After Track A, each rule owns its own
    chat id and its own `_sessions` entry.
    """
    import asyncio

    from api.stream import _sessions
    from db.models import RuleFiring
    from services import rule_engine
    from sqlalchemy import select

    db_factory = db_initialized

    rule_a, activity_a = await _seed_rule_with_activity_chat(
        db_factory,
        description="rule A — park on approval",
        spec={
            "source_tool": "slack__conversations_history",
            "source_args": {"channel": "D0123"},
            "filter": {
                "kind": "message_from_user_contains",
                "user": "Sepehr",
                "contains": ["bug"],
            },
            "action_prompt": "File a Jira bug.",
        },
        auto_approve=False,
    )
    rule_b, activity_b = await _seed_rule_with_activity_chat(
        db_factory,
        description="rule B — plain matched",
        spec=_slack_spec(user="Sepehr", contains=["ping"]),
        auto_approve=True,
    )
    assert activity_a != activity_b

    # Rule A: parks on a write tool call (pending_approval).
    mcp_a = fake_mcp_factory(
        tool_specs=[
            ("slack__conversations_history", "", {"type": "object", "properties": {}}),
            ("jira__create_issue", "", {"type": "object", "properties": {}}),
        ],
        responses={
            "slack__conversations_history": {
                "content": "",
                "is_error": False,
                "structured": {
                    "messages": [
                        {"user": "Sepehr", "text": "bug in billing", "ts": "1.0"},
                    ]
                },
            },
            "jira__create_issue": {
                "content": "PROJ-1",
                "is_error": False,
                "structured": None,
            },
        },
    )
    llm_a = fake_llm_factory(
        script=[
            [
                tool_call_chunk(
                    index=0,
                    id="call_pending_A",
                    name="jira__create_issue",
                    args_piece='{"summary":"bug"}',
                ),
                stop_chunk("tool_calls"),
            ],
        ]
    )
    firing_a = await asyncio.wait_for(
        rule_engine.tick(
            rule_a,
            mcp=mcp_a,
            llm=llm_a,
            settings=_fake_settings(),
            db_factory=db_factory,
            default_model="anthropic/claude-sonnet-4.6",
        ),
        timeout=2.0,
    )
    assert firing_a.status == "pending_approval"

    # Rule A's session is now parked under activity_a — NOT activity_b.
    assert activity_a in _sessions
    assert activity_b not in _sessions

    # Rule B: should run cleanly despite A being parked.
    mcp_b = fake_mcp_factory(
        responses={
            "slack__conversations_history": {
                "content": "",
                "is_error": False,
                "structured": {
                    "messages": [
                        {"user": "Sepehr", "text": "ping!", "ts": "2.0"},
                    ]
                },
            }
        }
    )
    llm_b = fake_llm_factory(
        script=[[token_chunk("pong"), stop_chunk("stop")]]
    )
    firing_b = await rule_engine.tick(
        rule_b,
        mcp=mcp_b,
        llm=llm_b,
        settings=_fake_settings(),
        db_factory=db_factory,
        default_model="anthropic/claude-sonnet-4.6",
    )
    assert firing_b.status == "matched"

    async with db_factory() as s:
        firings = (
            await s.execute(select(RuleFiring).order_by(RuleFiring.id))
        ).scalars().all()
        statuses = {(f.rule_id, f.status) for f in firings}
        assert (rule_a, "pending_approval") in statuses
        assert (rule_b, "matched") in statuses
        # each firing is tagged with its own rule's chat id
        conv_ids = {f.rule_id: f.conversation_id for f in firings}
        assert conv_ids[rule_a] == activity_a
        assert conv_ids[rule_b] == activity_b

    # Cleanup — let the parked A turn drain.
    _sessions[activity_a].cancel_pending()


async def test_firing_populates_duration_and_trigger_summary(
    db_initialized, fake_llm_factory, fake_mcp_factory
):
    from db.models import RuleFiring
    from services import rule_engine
    from sqlalchemy import select

    db_factory = db_initialized

    # Matched firing path: trigger_summary mirrors match_summary, duration_ms set.
    rule_match, _ = await _seed_rule_with_activity_chat(
        db_factory,
        description="populate metadata",
        spec=_slack_spec(user="Sepehr", contains=["ping"]),
        auto_approve=True,
    )
    mcp = fake_mcp_factory(
        responses={
            "slack__conversations_history": {
                "content": "",
                "is_error": False,
                "structured": {
                    "messages": [
                        {"user": "Sepehr", "text": "ping!", "ts": "1.0"},
                    ]
                },
            }
        }
    )
    llm = fake_llm_factory(script=[[token_chunk("pong"), stop_chunk("stop")]])

    firing = await rule_engine.tick(
        rule_match,
        mcp=mcp,
        llm=llm,
        settings=_fake_settings(),
        db_factory=db_factory,
        default_model="anthropic/claude-sonnet-4.6",
    )
    assert firing.status == "matched"

    async with db_factory() as s:
        saved = (
            await s.execute(
                select(RuleFiring).where(RuleFiring.rule_id == rule_match)
            )
        ).scalars().first()
        assert saved is not None
        assert saved.duration_ms is not None and saved.duration_ms >= 0
        assert saved.trigger_summary
        # Matched: trigger_summary reflects the match summary.
        assert saved.match_summary and saved.trigger_summary in saved.match_summary or (
            saved.trigger_summary == (saved.match_summary or "")[:140]
        )

    # Error firing path: trigger_summary captures first line of error text.
    rule_err, _ = await _seed_rule_with_activity_chat(
        db_factory,
        description="error trigger summary",
        spec=_slack_spec(),
    )
    mcp_err = RaisingMCP(error_text="boom-level-1\nboom-level-2")
    firing_err = await rule_engine.tick(
        rule_err,
        mcp=mcp_err,
        llm=fake_llm_factory(),
        settings=_fake_settings(),
        db_factory=db_factory,
        default_model="anthropic/claude-sonnet-4.6",
    )
    assert firing_err.status == "error"

    async with db_factory() as s:
        err_saved = (
            await s.execute(
                select(RuleFiring).where(RuleFiring.rule_id == rule_err)
            )
        ).scalars().first()
        assert err_saved is not None
        assert err_saved.trigger_summary == "boom-level-1"
        assert err_saved.duration_ms is not None
