"""Throttled streaming helpers for posting agent output to Telegram.

Telegram rate-limits ``editMessageText`` roughly to one edit per second per
chat; bursting through it trips 429s and stalls the bot for minutes. This
module buffers incoming tokens from ``AgentSession`` and flushes them with
two gates: a minimum interval between edits and a token batch size. Once
the buffer crosses ``TELEGRAM_MAX_MESSAGE_LEN`` we close off the current
placeholder at the last newline and open a new continuation message.
"""
from __future__ import annotations

import asyncio
from typing import Any, Protocol


# Tunables — deliberately module-level so tests can monkeypatch without
# digging into instance state.
TELEGRAM_MAX_MESSAGE_LEN = 4096
# Floor between editMessageText calls. Telegram allows ~1 req/sec per chat;
# 500ms gives headroom for retries inside aiogram's middleware.
MIN_EDIT_INTERVAL_SECONDS = 0.5
# Batch tokens before forcing a flush even if the interval hasn't elapsed.
FLUSH_TOKEN_THRESHOLD = 50


class _EditableBot(Protocol):
    """Minimum surface we call on the bot — lets us fake it in tests."""

    async def edit_message_text(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
    ) -> Any: ...

    async def send_message(
        self,
        *,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
    ) -> Any: ...


def _split_at_last_newline(text: str, limit: int) -> tuple[str, str]:
    """Split ``text`` at the last newline on or before ``limit``.

    Falls back to a hard slice at ``limit`` if no newline is present so
    long single-line code blocks don't wedge the stream.
    """
    head = text[:limit]
    cut = head.rfind("\n")
    if cut <= 0:
        # No newline in the first `limit` chars — cut hard.
        return text[:limit], text[limit:]
    return text[:cut], text[cut + 1 :]


class StreamingMessage:
    """Represents a single streaming reply on Telegram.

    Tokens are appended via :meth:`append`. The internal buffer is flushed
    to Telegram either on interval (``MIN_EDIT_INTERVAL_SECONDS``), on
    batch size (``FLUSH_TOKEN_THRESHOLD``), or when :meth:`flush` /
    :meth:`close` is called. When the buffer exceeds the Telegram message
    limit we edit the placeholder to the split prefix and open a new
    continuation message with ``reply_to_message_id``.
    """

    def __init__(
        self,
        bot: _EditableBot,
        chat_id: int,
        placeholder_message_id: int,
        *,
        min_interval: float = MIN_EDIT_INTERVAL_SECONDS,
        token_threshold: int = FLUSH_TOKEN_THRESHOLD,
        max_len: int = TELEGRAM_MAX_MESSAGE_LEN,
    ) -> None:
        self._bot = bot
        self._chat_id = int(chat_id)
        self._current_message_id = int(placeholder_message_id)
        self._min_interval = float(min_interval)
        self._token_threshold = int(token_threshold)
        self._max_len = int(max_len)

        self._buffer: str = ""  # chars accumulated for the current message
        self._last_edit_text: str = ""  # dedupe "message is not modified" errors
        self._pending_tokens: int = 0
        self._last_edit_time: float = 0.0
        self._lock = asyncio.Lock()
        self._closed = False

    async def append(self, token: str) -> None:
        """Buffer a token and opportunistically flush."""
        if self._closed or not token:
            return
        self._buffer += token
        self._pending_tokens += 1

        interval_elapsed = (
            asyncio.get_event_loop().time() - self._last_edit_time
        ) >= self._min_interval
        batch_full = self._pending_tokens >= self._token_threshold
        overflow = len(self._buffer) >= self._max_len

        if overflow or (interval_elapsed and batch_full):
            await self.flush()

    async def flush(self) -> None:
        """Push whatever is buffered to Telegram right now."""
        if self._closed:
            return
        async with self._lock:
            # Recheck under the lock — another caller may have flushed us.
            if not self._buffer:
                return

            # Spill until buffer is under the per-message cap.
            while len(self._buffer) > self._max_len:
                head, tail = _split_at_last_newline(self._buffer, self._max_len)
                await self._safe_edit(head)
                # Open a continuation message. Empty placeholder text is
                # rejected by Telegram, so seed with a single zero-width
                # space and let the next append do the first real edit.
                reply = await self._bot.send_message(
                    chat_id=self._chat_id,
                    text="​",
                    reply_to_message_id=self._current_message_id,
                )
                self._current_message_id = int(_extract_message_id(reply))
                self._last_edit_text = ""
                self._buffer = tail

            if self._buffer:
                await self._safe_edit(self._buffer)

            self._pending_tokens = 0
            self._last_edit_time = asyncio.get_event_loop().time()

    async def close(self) -> None:
        """Final flush for end-of-turn cleanup."""
        try:
            await self.flush()
        finally:
            self._closed = True

    async def _safe_edit(self, text: str) -> None:
        """Edit, swallowing 'message is not modified' spam silently."""
        if text == self._last_edit_text:
            return
        try:
            await self._bot.edit_message_text(
                chat_id=self._chat_id,
                message_id=self._current_message_id,
                text=text,
            )
            self._last_edit_text = text
        except Exception as exc:  # noqa: BLE001 - want any provider error logged
            msg = str(exc).lower()
            if "message is not modified" in msg:
                # Harmless — nothing to do.
                return
            # Re-raise anything else so the bridge can decide whether the
            # whole turn should abort; aiogram's retry middleware covers
            # 429 transparently before we see it here.
            raise


def _extract_message_id(reply: Any) -> int:
    """Read message_id off an aiogram `Message` or any dict-shaped fake."""
    if reply is None:
        raise RuntimeError("bot.send_message returned None")
    if isinstance(reply, dict):
        mid = reply.get("message_id") or reply.get("id")
        if mid is None:
            raise RuntimeError("fake bot reply missing message_id")
        return int(mid)
    mid = getattr(reply, "message_id", None)
    if mid is None:
        raise RuntimeError("bot reply missing message_id attribute")
    return int(mid)
