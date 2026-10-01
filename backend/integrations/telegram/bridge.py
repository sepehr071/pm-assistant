"""Glue between Telegram updates and the shared AgentSession.

The bridge owns:

* Looking up or creating a ``Conversation(kind="telegram")`` per bound
  user (single-user app → at most one row).
* Constructing an ``AgentSession`` via ``api.stream.build_agent_session``
  and registering it in ``api.stream._sessions`` under the conversation
  id so that callbacks (approve/reject) land on the same session the
  HTTP web client uses.
* Running a single turn and translating the SSE-style events emitted by
  ``AgentSession`` into Telegram sends/edits/keyboards.
* Validating callback queries against the currently bound
  ``telegram_chat_id`` before flipping the in-memory approval gate.

The module never imports aiogram at call time so ``bot.py`` can stay
thin and unit tests can use a fake bot. Any type we actually need from
aiogram is referenced lazily or through a small Protocol.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from agent.loop import AgentSession
from db.models import Conversation
from db.session import async_session_factory
from integrations.telegram import pairing
from integrations.telegram.streaming import StreamingMessage


logger = logging.getLogger(__name__)


# User-facing copy is centralised so tests (and later i18n) can reach it.
PAIRING_PROMPT = (
    "This bot is not paired yet. Generate a pairing code in the PM "
    "Assistant web UI (Settings → Telegram gateway) and send "
    "/pair <code> here."
)
PAIRED_OK = "Paired — you're good to go."
PAIRING_BAD_USAGE = "Usage: /pair <CODE>"
PAIRING_NO_CODE = "No pairing code pending. Generate one in the web UI first."
PAIRING_MISMATCH = "That code doesn't match. Try again."
PAIRING_EXPIRED = "That code expired. Generate a fresh one in the web UI."
PAIRING_UNAUTHORIZED = (
    "This bot is bound to a different chat. Unpair in the web UI to "
    "reset the binding."
)
START_MESSAGE = (
    "Hi — I'm your PM Assistant on Telegram. "
    "Send /pair <code> with a code from the web UI to get started."
)
PENDING_EMPTY = "No pending approvals."
RESET_DONE = (
    "Conversation reset. Start fresh — I won't see anything from before."
)
RESET_NOT_BOUND = PAIRING_PROMPT


# Callback-data prefixes. Keep them short — Telegram caps callback_data
# at 64 bytes and we don't want to waste any on plumbing.
CB_APPROVE = "approve:"
CB_REJECT = "reject:"


class _BotLike(Protocol):
    async def send_message(
        self,
        *,
        chat_id: int,
        text: str,
        reply_markup: Any | None = None,
        reply_to_message_id: int | None = None,
    ) -> Any: ...

    async def edit_message_text(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: Any | None = None,
    ) -> Any: ...


def _inline_keyboard_for_approval(tool_call_id: str) -> Any:
    """Build the Approve/Reject inline keyboard for a tool call.

    Late import of aiogram types so this module stays importable without
    the dependency present (tests can pass a plain dict through a fake
    bot).
    """
    try:
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Approve", callback_data=f"{CB_APPROVE}{tool_call_id}"
                    ),
                    InlineKeyboardButton(
                        text="Reject", callback_data=f"{CB_REJECT}{tool_call_id}"
                    ),
                ]
            ]
        )
    except Exception:  # pragma: no cover - aiogram absent in some test paths
        # Plain-dict fallback keeps unit tests moving.
        return {
            "inline_keyboard": [
                [
                    {"text": "Approve", "callback_data": f"{CB_APPROVE}{tool_call_id}"},
                    {"text": "Reject", "callback_data": f"{CB_REJECT}{tool_call_id}"},
                ]
            ]
        }


async def _find_or_create_conversation(
    db_factory: async_sessionmaker[AsyncSession],
    telegram_chat_id: int,
    telegram_username: str | None,
) -> int:
    """Return the conversation id for this Telegram binding.

    For the single-user app we store exactly one ``Conversation(kind="telegram")``.
    If it doesn't exist yet we create it; the Telegram chat id itself is
    kept in ``UserSetting`` (pairing module), so we don't need to
    persist it on the conversation row.
    """
    async with db_factory() as session:
        stmt = (
            select(Conversation)
            .where(Conversation.kind == "telegram")
            .order_by(Conversation.created_at)
        )
        existing = (await session.execute(stmt)).scalars().first()
        if existing is not None:
            return int(existing.id)  # type: ignore[arg-type]
        title = f"Telegram: @{telegram_username}" if telegram_username else "Telegram"
        convo = Conversation(title=title, kind="telegram")
        session.add(convo)
        await session.commit()
        await session.refresh(convo)
        return int(convo.id)  # type: ignore[arg-type]


class TelegramBridge:
    """Drives one Telegram DM through the agent loop."""

    def __init__(
        self,
        *,
        bot: _BotLike,
        app_state: Any,
        db_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._bot = bot
        self._app_state = app_state
        self._db_factory = db_factory or async_session_factory
        # Cache Telegram chat_id -> Conversation.id so every incoming DM
        # doesn't re-read the DB. Safe because the app is single-user.
        self._conv_id_cache: dict[int, int] = {}
        # One active turn per Telegram chat. If the user sends a new DM
        # while a turn is still running we queue the new turn after the
        # old one rather than spinning up two against the same session.
        self._turn_locks: dict[int, asyncio.Lock] = {}
        # Message-id of the inline-keyboard approval prompt for a given
        # tool_call_id so we can edit it in place once the user clicks.
        self._approval_prompts: dict[str, tuple[int, int]] = {}

    # ---------- public entry points called from bot.py ------------------

    async def handle_start(self, *, telegram_chat_id: int) -> None:
        await self._bot.send_message(chat_id=telegram_chat_id, text=START_MESSAGE)

    async def handle_pair(
        self,
        *,
        telegram_chat_id: int,
        code: str | None,
        telegram_username: str | None,
    ) -> None:
        if not code:
            await self._bot.send_message(
                chat_id=telegram_chat_id, text=PAIRING_BAD_USAGE
            )
            return
        ok, reason = await pairing.try_bind(
            self._db_factory,
            code=code,
            telegram_chat_id=telegram_chat_id,
            telegram_username=telegram_username,
        )
        if ok:
            await self._bot.send_message(chat_id=telegram_chat_id, text=PAIRED_OK)
            return
        await self._bot.send_message(
            chat_id=telegram_chat_id, text=_explain_pair_failure(reason)
        )

    async def handle_reset(self, *, telegram_chat_id: int) -> None:
        """Wipe the visible history for the bound Telegram conversation.

        Sets ``Conversation.history_cutoff_at = now`` and drops the
        cached AgentSession so the next message builds a fresh one with
        a regenerated CAPABILITIES table. Non-destructive: messages stay
        in the DB.
        """
        binding = await pairing.read_binding(self._db_factory)
        bound_chat = binding.get("chat_id")
        if bound_chat != telegram_chat_id:
            await self._bot.send_message(
                chat_id=telegram_chat_id, text=RESET_NOT_BOUND
            )
            return

        conv_id = await self._resolve_conversation_id(
            telegram_chat_id, binding.get("username")
        )

        # 1. Always persist the cutoff on the Conversation row so the
        #    next turn (whichever session builds it) filters prior
        #    messages out of the LLM context.
        from datetime import UTC, datetime

        from db.models import Conversation as _Conversation

        async with self._db_factory() as db_session:
            convo = await db_session.get(_Conversation, conv_id)
            if convo is not None:
                convo.history_cutoff_at = datetime.now(UTC)
                db_session.add(convo)
                await db_session.commit()

        # 2. Drop any cached session so the next turn rebuilds with
        #    fresh CAPABILITIES and an empty message trail. Cancel any
        #    pending approvals on the way out.
        from api.stream import _sessions  # local import avoids cycles

        session = _sessions.pop(conv_id, None)
        if session is not None:
            cancel = getattr(session, "cancel_pending", None)
            if callable(cancel):
                try:
                    cancel()
                except Exception:
                    pass

        # 3. Approval prompts attached to the cancelled session no
        #    longer reference a live turn — clear them so a stale
        #    callback can't land against the new session.
        self._approval_prompts.clear()

        await self._bot.send_message(chat_id=telegram_chat_id, text=RESET_DONE)

    async def handle_pending(self, *, telegram_chat_id: int) -> None:
        binding = await pairing.read_binding(self._db_factory)
        bound_chat = binding.get("chat_id")
        if bound_chat != telegram_chat_id:
            await self._bot.send_message(chat_id=telegram_chat_id, text=PAIRING_PROMPT)
            return

        conv_id = await self._resolve_conversation_id(
            telegram_chat_id, binding.get("username")
        )
        from api.stream import _sessions  # local import avoids cycles

        session = _sessions.get(conv_id)
        pending = list(session.pending.keys()) if session else []
        if not pending:
            await self._bot.send_message(chat_id=telegram_chat_id, text=PENDING_EMPTY)
            return
        lines = [f"Pending approvals ({len(pending)}):"]
        lines.extend(f"- {tcid}" for tcid in pending)
        await self._bot.send_message(
            chat_id=telegram_chat_id, text="\n".join(lines)
        )

    async def handle_text(
        self,
        *,
        telegram_chat_id: int,
        telegram_user_id: int,
        text: str,
        telegram_username: str | None,
    ) -> None:
        """Translate a Telegram DM into a fresh agent turn."""
        binding = await pairing.read_binding(self._db_factory)
        bound_chat = binding.get("chat_id")
        if bound_chat != telegram_chat_id:
            await self._bot.send_message(chat_id=telegram_chat_id, text=PAIRING_PROMPT)
            return

        conv_id = await self._resolve_conversation_id(
            telegram_chat_id, binding.get("username") or telegram_username
        )

        # Serialize turns per conversation so approvals don't interleave
        # with new user messages.
        lock = self._turn_locks.setdefault(conv_id, asyncio.Lock())
        async with lock:
            await self._run_turn(
                telegram_chat_id=telegram_chat_id,
                conv_id=conv_id,
                text=text,
            )

    async def handle_callback(
        self,
        *,
        telegram_chat_id: int,
        telegram_user_id: int,
        callback_message_id: int | None,
        data: str,
    ) -> tuple[bool, str]:
        """Process an approve:/reject: callback_data.

        Returns ``(ok, user_facing_answer)`` — ``answerCallbackQuery`` in
        bot.py renders the answer as a toast regardless of ok.
        """
        binding = await pairing.read_binding(self._db_factory)
        bound_chat = binding.get("chat_id")
        if bound_chat != telegram_chat_id:
            return False, "Not your bot."

        if data.startswith(CB_APPROVE):
            tcid = data[len(CB_APPROVE) :]
            approved = True
        elif data.startswith(CB_REJECT):
            tcid = data[len(CB_REJECT) :]
            approved = False
        else:
            return False, "Unknown action."

        conv_id = await self._resolve_conversation_id(
            telegram_chat_id, binding.get("username")
        )
        from api.stream import _sessions

        session = _sessions.get(conv_id)
        if session is None:
            return False, "Session expired."

        ok = session.approve(tcid, approved)
        # Edit the prompt message in place so the user can see the choice
        # landed even if we later fail to answer the callback.
        prompt = self._approval_prompts.pop(tcid, None)
        if prompt is not None:
            chat_id_for_edit, msg_id = prompt
            try:
                await self._bot.edit_message_text(
                    chat_id=chat_id_for_edit,
                    message_id=msg_id,
                    text=("✓ Approved" if approved else "✗ Rejected"),
                    reply_markup=None,
                )
            except Exception:  # noqa: BLE001 - edit failures are cosmetic
                logger.debug(
                    "telegram bridge: failed to edit approval prompt", exc_info=True
                )

        if not ok:
            return False, "Already resolved."
        return True, "Approved" if approved else "Rejected"

    # ---------- internals -----------------------------------------------

    async def _resolve_conversation_id(
        self, telegram_chat_id: int, telegram_username: str | None
    ) -> int:
        cached = self._conv_id_cache.get(telegram_chat_id)
        if cached is not None:
            return cached
        conv_id = await _find_or_create_conversation(
            self._db_factory, telegram_chat_id, telegram_username
        )
        self._conv_id_cache[telegram_chat_id] = conv_id
        return conv_id

    async def _get_or_create_agent_session(self, conv_id: int) -> AgentSession:
        from api import stream as stream_module

        # We intentionally read/write `_sessions` directly instead of
        # going through `_get_or_create_session` — that helper takes a
        # FastAPI Request, which we don't have here. The lock held on
        # the HTTP path is only required to de-duplicate concurrent
        # creations for the same chat id; our per-conv lock in
        # `_turn_locks` already serializes turns.
        existing = stream_module._sessions.get(conv_id)
        if existing is not None:
            return existing
        session = await stream_module.build_agent_session(conv_id, self._app_state)
        stream_module._sessions[conv_id] = session
        return session

    async def _run_turn(
        self, *, telegram_chat_id: int, conv_id: int, text: str
    ) -> None:
        try:
            agent_session = await self._get_or_create_agent_session(conv_id)
        except Exception as exc:  # noqa: BLE001 - network-ish errors surface here
            logger.exception("telegram bridge: failed to build agent session")
            await self._bot.send_message(
                chat_id=telegram_chat_id,
                text=f"Couldn't start a turn: {exc}",
            )
            return

        placeholder = await self._bot.send_message(
            chat_id=telegram_chat_id, text="…"
        )
        placeholder_id = _message_id(placeholder)
        stream = StreamingMessage(
            bot=self._bot,  # type: ignore[arg-type]
            chat_id=telegram_chat_id,
            placeholder_message_id=placeholder_id,
        )

        try:
            async for event in agent_session.run_turn(text):
                await self._dispatch_event(event, telegram_chat_id, stream)
        except Exception as exc:  # noqa: BLE001 - want to surface any crash
            logger.exception("telegram bridge: run_turn crashed")
            await self._bot.send_message(
                chat_id=telegram_chat_id, text=f"Error: {exc}"
            )
        finally:
            await stream.close()

    async def _dispatch_event(
        self,
        event: dict[str, Any],
        telegram_chat_id: int,
        stream: StreamingMessage,
    ) -> None:
        etype = event.get("type")
        if etype == "token":
            delta = event.get("delta", "")
            if delta:
                await stream.append(delta)
            return
        if etype == "tool_call_request":
            await stream.flush()
            tcid = event.get("tool_call_id") or ""
            tool_name = event.get("tool_name") or event.get("name") or "tool"
            reply = await self._bot.send_message(
                chat_id=telegram_chat_id,
                text=f"Approval required: {tool_name}",
                reply_markup=_inline_keyboard_for_approval(tcid),
            )
            self._approval_prompts[tcid] = (telegram_chat_id, _message_id(reply))
            return
        if etype == "tool_call_result":
            tool_name = event.get("tool_name") or event.get("name") or "tool"
            is_error = bool(event.get("is_error"))
            raw_result = event.get("result") or ""
            result_text = raw_result if isinstance(raw_result, str) else str(raw_result)
            # Preview caps at ~200 chars. A silent "✓ Ran" on a failed or
            # empty call makes it look to the user like the tool worked,
            # which is exactly what poisons the next LLM turn.
            preview = result_text.strip().splitlines()[0] if result_text else ""
            if len(preview) > 200:
                preview = preview[:197] + "…"
            if is_error:
                msg = f"⚠ {tool_name} failed"
                if preview:
                    msg += f": {preview}"
            elif not result_text.strip():
                msg = f"✓ Ran {tool_name} (no output)"
            else:
                msg = f"✓ Ran {tool_name}"
            await self._bot.send_message(
                chat_id=telegram_chat_id,
                text=msg,
            )
            return
        if etype == "error":
            err = event.get("error") or "Unknown error."
            await self._bot.send_message(
                chat_id=telegram_chat_id, text=f"Error: {err}"
            )
            return
        if etype == "done":
            await stream.flush()
            return
        if etype == "tool_call_start":
            # Optional signal — we don't render it yet but ignoring it
            # is safe because the eventual `tool_call_result` covers UX.
            return


def _explain_pair_failure(reason: str) -> str:
    if reason == "no_pending_code":
        return PAIRING_NO_CODE
    if reason == "code_mismatch":
        return PAIRING_MISMATCH
    if reason == "code_expired":
        return PAIRING_EXPIRED
    return PAIRING_MISMATCH


def _message_id(message: Any) -> int:
    if message is None:
        raise RuntimeError("bot returned None for a send_message call")
    if isinstance(message, dict):
        mid = message.get("message_id") or message.get("id")
        if mid is None:
            raise RuntimeError("fake bot message missing message_id")
        return int(mid)
    mid = getattr(message, "message_id", None)
    if mid is None:
        raise RuntimeError("bot message missing message_id attribute")
    return int(mid)
