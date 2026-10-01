from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from integrations.telegram import pairing
from integrations.telegram.bridge import (
    CB_APPROVE,
    CB_REJECT,
    PAIRED_OK,
    PAIRING_PROMPT,
    TelegramBridge,
)
from integrations.telegram.streaming import StreamingMessage


pytestmark = pytest.mark.asyncio


# ---------- fake aiogram surface ---------------------------------------------


@dataclass
class _SentMessage:
    chat_id: int
    text: str
    reply_markup: Any = None
    reply_to_message_id: int | None = None


@dataclass
class _EditRecord:
    chat_id: int
    message_id: int
    text: str
    reply_markup: Any = None


class FakeBot:
    """In-memory stand-in for aiogram.Bot used by the bridge and stream."""

    def __init__(self) -> None:
        self.sent: list[_SentMessage] = []
        self.edits: list[_EditRecord] = []
        self._next_id = 1000

    async def send_message(
        self,
        *,
        chat_id: int,
        text: str,
        reply_markup: Any = None,
        reply_to_message_id: int | None = None,
    ) -> dict[str, Any]:
        self._next_id += 1
        self.sent.append(
            _SentMessage(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                reply_to_message_id=reply_to_message_id,
            )
        )
        return {"message_id": self._next_id, "chat_id": chat_id, "text": text}

    async def edit_message_text(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: Any = None,
    ) -> dict[str, Any]:
        self.edits.append(
            _EditRecord(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=reply_markup,
            )
        )
        return {"message_id": message_id, "chat_id": chat_id, "text": text}


# ---------- fake AgentSession event generator --------------------------------


class FakeAgentSession:
    """Emits a scripted sequence of SSE-style events for one turn."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = list(events)
        self.pending: dict[str, Any] = {}
        self.approvals: list[tuple[str, bool]] = []
        self.calls_with: list[str] = []
        self.resets: list[Any] = []

    def approve(self, tool_call_id: str, approved: bool) -> bool:
        self.approvals.append((tool_call_id, approved))
        self.pending.pop(tool_call_id, None)
        return True

    def cancel_pending(self) -> None:
        self.pending.clear()

    async def reset_history(self, cutoff: Any = None) -> Any:
        from datetime import UTC, datetime

        # Mirror the real AgentSession contract: write a cutoff on the
        # Conversation row so _load_history will filter prior messages.
        effective = cutoff or datetime.now(UTC)
        self.resets.append(effective)
        self.cancel_pending()
        # The bridge relies on the DB write actually happening for the
        # test assertion, so defer to the session-less path.
        # (Bridge's handle_reset only calls DB write when session is None;
        # when session is present we need the fake to do the write too.)
        return effective

    async def run_turn(self, text: str):
        self.calls_with.append(text)
        for evt in self._events:
            await asyncio.sleep(0)
            yield evt


# ---------- helper: app_state stub ------------------------------------------


class _Settings:
    openrouter_default_model = "m"


class _AppState:
    def __init__(self) -> None:
        self.settings = _Settings()
        self.llm = object()
        self.mcp = object()


# ---------- tests ------------------------------------------------------------


async def test_unbound_dm_gets_pairing_prompt(db_initialized) -> None:
    bot = FakeBot()
    bridge = TelegramBridge(
        bot=bot,
        app_state=_AppState(),
        db_factory=db_initialized,
    )
    await bridge.handle_text(
        telegram_chat_id=42,
        telegram_user_id=42,
        text="hello",
        telegram_username="pm",
    )
    assert len(bot.sent) == 1
    assert bot.sent[0].text == PAIRING_PROMPT


async def test_pair_command_binds(db_initialized) -> None:
    bot = FakeBot()
    bridge = TelegramBridge(
        bot=bot, app_state=_AppState(), db_factory=db_initialized
    )
    code, _ = await pairing.create_pairing_code(db_initialized)
    await bridge.handle_pair(
        telegram_chat_id=7, code=code, telegram_username="pm"
    )
    assert bot.sent[-1].text == PAIRED_OK
    binding = await pairing.read_binding(db_initialized)
    assert binding["chat_id"] == 7


async def test_bound_dm_creates_conversation_and_runs_turn(
    db_initialized, monkeypatch
) -> None:
    bot = FakeBot()
    bridge = TelegramBridge(
        bot=bot, app_state=_AppState(), db_factory=db_initialized
    )
    # Bind first.
    code, _ = await pairing.create_pairing_code(db_initialized)
    await pairing.try_bind(
        db_initialized,
        code=code,
        telegram_chat_id=555,
        telegram_username="pm",
    )

    # Stub the session builder so we don't need a real LLM/MCP.
    fake = FakeAgentSession(
        [
            {"type": "token", "event_id": 1, "delta": "Hello"},
            {"type": "token", "event_id": 2, "delta": " world"},
            {"type": "done", "event_id": 3, "message_id": 99},
        ]
    )

    from api import stream as stream_module

    async def _fake_build(chat_id: int, app_state: Any) -> FakeAgentSession:
        return fake

    monkeypatch.setattr(stream_module, "build_agent_session", _fake_build)
    stream_module._sessions.clear()

    await bridge.handle_text(
        telegram_chat_id=555,
        telegram_user_id=555,
        text="plan my sprint",
        telegram_username="pm",
    )

    # The agent must have run exactly once with the original user text.
    assert fake.calls_with == ["plan my sprint"]
    # A "telegram" conversation row exists.
    from db.models import Conversation
    from sqlalchemy import select

    async with db_initialized() as session:
        convos = (
            await session.execute(
                select(Conversation).where(Conversation.kind == "telegram")
            )
        ).scalars().all()
        assert len(convos) == 1
    # Placeholder was sent, tokens flushed via edits, no error surfaced.
    assert any("…" == m.text or m.text.startswith("…") for m in bot.sent)
    assert any("Hello world" in e.text for e in bot.edits)


async def test_callback_query_approves_in_session(db_initialized, monkeypatch) -> None:
    bot = FakeBot()
    bridge = TelegramBridge(
        bot=bot, app_state=_AppState(), db_factory=db_initialized
    )
    code, _ = await pairing.create_pairing_code(db_initialized)
    await pairing.try_bind(
        db_initialized,
        code=code,
        telegram_chat_id=777,
        telegram_username="pm",
    )

    # Seed a fake session with one pending approval.
    from api import stream as stream_module

    fake = FakeAgentSession([])
    fake.pending["tc_42"] = object()

    # Register it under the conversation id the bridge will resolve to.
    # Force creation by asking the bridge to resolve first.
    conv_id = await bridge._resolve_conversation_id(777, "pm")
    stream_module._sessions.clear()
    stream_module._sessions[conv_id] = fake  # type: ignore[assignment]

    # Simulate the approval prompt having been posted previously so the
    # bridge has a message to edit.
    bridge._approval_prompts["tc_42"] = (777, 314)

    ok, answer = await bridge.handle_callback(
        telegram_chat_id=777,
        telegram_user_id=777,
        callback_message_id=314,
        data=f"{CB_APPROVE}tc_42",
    )
    assert ok is True
    assert answer == "Approved"
    assert fake.approvals == [("tc_42", True)]
    # The prompt message was edited to the confirmation text.
    assert any(
        e.message_id == 314 and e.text.startswith("✓ Approved") for e in bot.edits
    )


async def test_callback_query_rejects_other_chat(db_initialized) -> None:
    bot = FakeBot()
    bridge = TelegramBridge(
        bot=bot, app_state=_AppState(), db_factory=db_initialized
    )
    code, _ = await pairing.create_pairing_code(db_initialized)
    await pairing.try_bind(
        db_initialized,
        code=code,
        telegram_chat_id=777,
        telegram_username="pm",
    )
    ok, answer = await bridge.handle_callback(
        telegram_chat_id=999,  # not the bound chat
        telegram_user_id=999,
        callback_message_id=1,
        data=f"{CB_APPROVE}tc",
    )
    assert ok is False
    assert "Not your bot." in answer


async def test_streaming_throttle_batches_tokens() -> None:
    bot = FakeBot()
    stream = StreamingMessage(
        bot=bot,
        chat_id=1,
        placeholder_message_id=42,
        min_interval=0.0,
        token_threshold=5,
        max_len=4096,
    )
    # Three tokens — below threshold, should NOT have edited yet.
    await stream.append("a")
    await stream.append("b")
    await stream.append("c")
    assert bot.edits == []
    # Two more tokens — hit the batch threshold, one flush happens.
    await stream.append("d")
    await stream.append("e")
    assert len(bot.edits) == 1
    assert bot.edits[-1].text == "abcde"


async def test_reset_command_clears_session_and_sets_cutoff(
    db_initialized,
) -> None:
    """/reset drops the cached AgentSession and writes a cutoff so the
    next turn starts from scratch without replaying a poisoned
    history."""
    from datetime import UTC, datetime

    from db.models import Conversation

    bot = FakeBot()
    bridge = TelegramBridge(
        bot=bot, app_state=_AppState(), db_factory=db_initialized
    )
    # Bind the bot.
    code, _ = await pairing.create_pairing_code(db_initialized)
    await pairing.try_bind(
        db_initialized,
        code=code,
        telegram_chat_id=42,
        telegram_username="pm",
    )

    # Seed a fake session in the shared registry.
    from api import stream as stream_module

    conv_id = await bridge._resolve_conversation_id(42, "pm")
    fake = FakeAgentSession([])
    fake.pending["tc_live"] = object()  # pretend a gate was open
    stream_module._sessions.clear()
    stream_module._sessions[conv_id] = fake  # type: ignore[assignment]
    bridge._approval_prompts["tc_live"] = (42, 1)

    await bridge.handle_reset(telegram_chat_id=42)

    # Session removed from registry, pending approvals cleared.
    assert conv_id not in stream_module._sessions
    assert bridge._approval_prompts == {}
    # Bot confirmed the reset.
    assert any("reset" in m.text.lower() for m in bot.sent)

    # Cutoff persisted on the conversation row.
    async with db_initialized() as session:
        convo = await session.get(Conversation, conv_id)
        assert convo is not None
        assert convo.history_cutoff_at is not None
        # Cutoff should be recent (within ~5 seconds of now). SQLite
        # strips tzinfo on round-trip, so normalise both sides.
        stored = convo.history_cutoff_at
        if stored.tzinfo is None:
            stored = stored.replace(tzinfo=UTC)
        delta = abs((datetime.now(UTC) - stored).total_seconds())
        assert delta < 5


async def test_reset_from_unbound_chat_rejected(db_initialized) -> None:
    """A /reset from a chat that isn't the bound one must never touch
    the conversation or drop any session."""
    bot = FakeBot()
    bridge = TelegramBridge(
        bot=bot, app_state=_AppState(), db_factory=db_initialized
    )
    await bridge.handle_reset(telegram_chat_id=999)
    # Only a pairing-prompt reply; no DB write, no session changes.
    assert len(bot.sent) == 1
    assert bot.sent[0].text == PAIRING_PROMPT


async def test_tool_call_error_rendered_with_preview(db_initialized) -> None:
    """An error tool_call_result surfaces a preview of the failure
    string so the user doesn't see a silent ✓ when the tool failed."""
    bot = FakeBot()
    bridge = TelegramBridge(
        bot=bot, app_state=_AppState(), db_factory=db_initialized
    )
    stream = StreamingMessage(
        bot=bot,
        chat_id=77,
        placeholder_message_id=1,
        min_interval=0.0,
        token_threshold=1,
    )

    await bridge._dispatch_event(
        {
            "type": "tool_call_result",
            "tool_name": "slack__find_users",
            "is_error": True,
            "result": "OAuth token expired: please reconnect",
        },
        telegram_chat_id=77,
        stream=stream,
    )

    # Two messages sent: the placeholder created by StreamingMessage
    # isn't our concern — check that the LAST sent message is the
    # warning with the preview, not a silent ✓.
    tool_msgs = [m for m in bot.sent if m.chat_id == 77]
    assert any(
        m.text.startswith("⚠ slack__find_users failed")
        and "OAuth token expired" in m.text
        for m in tool_msgs
    )


async def test_tool_call_success_still_renders_check(db_initialized) -> None:
    """Clean tool results still use the ✓ marker (regression guard)."""
    bot = FakeBot()
    bridge = TelegramBridge(
        bot=bot, app_state=_AppState(), db_factory=db_initialized
    )
    stream = StreamingMessage(
        bot=bot,
        chat_id=77,
        placeholder_message_id=1,
        min_interval=0.0,
        token_threshold=1,
    )

    await bridge._dispatch_event(
        {
            "type": "tool_call_result",
            "tool_name": "github__list_prs",
            "is_error": False,
            "result": "3 PRs open",
        },
        telegram_chat_id=77,
        stream=stream,
    )

    tool_msgs = [m for m in bot.sent if m.chat_id == 77]
    assert any(m.text == "✓ Ran github__list_prs" for m in tool_msgs)


async def test_message_over_4096_chars_splits() -> None:
    bot = FakeBot()
    stream = StreamingMessage(
        bot=bot,
        chat_id=9,
        placeholder_message_id=100,
        min_interval=0.0,
        token_threshold=1,
        max_len=20,  # tiny cap so we can trigger a split deterministically
    )
    # Build a payload that clearly overflows and has newlines to split on.
    payload = "line-one xx\nline-two yy\nline-three-very-long-suffix\n"
    for ch in payload:
        await stream.append(ch)
    await stream.flush()

    # At least one continuation message was opened via send_message.
    continuation_sends = [m for m in bot.sent if m.reply_to_message_id == 100]
    assert len(continuation_sends) >= 1
    # And edits landed across both the original placeholder and the
    # continuation message.
    placeholders_edited = {e.message_id for e in bot.edits}
    assert 100 in placeholders_edited
    assert len(placeholders_edited) >= 2
