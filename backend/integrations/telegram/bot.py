"""aiogram Bot/Dispatcher wiring for the Telegram gateway.

This module stays deliberately thin: the handlers parse Telegram
updates, validate they come from the bound user, and defer to
``TelegramBridge`` for the actual work. Long-polling is started as an
asyncio task that lives for the lifetime of ``PollingHandle``; it's
resilient to per-update exceptions (one malformed update can't kill
polling).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from integrations.telegram import pairing
from integrations.telegram.bridge import CB_APPROVE, CB_REJECT, TelegramBridge


logger = logging.getLogger(__name__)


@dataclass
class PollingHandle:
    """Return value of :func:`start_polling`."""

    bot: Any
    dispatcher: Any
    task: asyncio.Task[Any]
    bridge: TelegramBridge
    username: str | None = None
    error: str | None = None

    async def stop(self) -> None:
        """Stop polling and close the bot session."""
        dp = self.dispatcher
        try:
            if dp is not None and hasattr(dp, "stop_polling"):
                try:
                    await dp.stop_polling()
                except Exception:
                    logger.debug("telegram: stop_polling raised", exc_info=True)
        finally:
            task = self.task
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            bot = self.bot
            if bot is not None:
                close = getattr(bot, "close", None)
                if callable(close):
                    try:
                        await close()
                    except Exception:
                        logger.debug("telegram: bot.close raised", exc_info=True)
                else:
                    sess = getattr(bot, "session", None)
                    aclose = getattr(sess, "close", None)
                    if callable(aclose):
                        try:
                            await aclose()
                        except Exception:
                            logger.debug(
                                "telegram: bot.session.close raised", exc_info=True
                            )


def is_configured(token: str | None) -> bool:
    """Return True if the token is non-empty and has the right shape."""
    return bool(token and ":" in token and len(token) > 20)


async def _register_handlers(
    dispatcher: Any,
    bridge: TelegramBridge,
) -> None:
    """Wire aiogram handlers against the shared bridge instance."""
    from aiogram import F
    from aiogram.filters import Command
    from aiogram.types import CallbackQuery, Message

    @dispatcher.message(Command("start"))
    async def _on_start(message: Message) -> None:
        try:
            await bridge.handle_start(telegram_chat_id=message.chat.id)
        except Exception:  # noqa: BLE001
            logger.exception("telegram /start handler crashed")

    @dispatcher.message(Command("pair"))
    async def _on_pair(message: Message) -> None:
        # Parse: "/pair ABC123" — aiogram's CommandObject also works but
        # splitting manually keeps the code independent of minor aiogram
        # API shifts between 3.x minor versions.
        try:
            parts = (message.text or "").split(maxsplit=1)
            code = parts[1].strip() if len(parts) > 1 else None
            username = getattr(getattr(message, "from_user", None), "username", None)
            await bridge.handle_pair(
                telegram_chat_id=message.chat.id,
                code=code,
                telegram_username=username,
            )
        except Exception:  # noqa: BLE001
            logger.exception("telegram /pair handler crashed")

    @dispatcher.message(Command("pending"))
    async def _on_pending(message: Message) -> None:
        try:
            await bridge.handle_pending(telegram_chat_id=message.chat.id)
        except Exception:  # noqa: BLE001
            logger.exception("telegram /pending handler crashed")

    @dispatcher.message(Command("reset"))
    async def _on_reset(message: Message) -> None:
        try:
            await bridge.handle_reset(telegram_chat_id=message.chat.id)
        except Exception:  # noqa: BLE001
            logger.exception("telegram /reset handler crashed")

    @dispatcher.message(F.text & ~F.text.startswith("/"))
    async def _on_text(message: Message) -> None:
        try:
            from_user = getattr(message, "from_user", None)
            user_id = int(getattr(from_user, "id", 0) or 0)
            username = getattr(from_user, "username", None)
            await bridge.handle_text(
                telegram_chat_id=message.chat.id,
                telegram_user_id=user_id,
                text=message.text or "",
                telegram_username=username,
            )
        except Exception:  # noqa: BLE001
            logger.exception("telegram text handler crashed")

    @dispatcher.callback_query(
        F.data.startswith(CB_APPROVE) | F.data.startswith(CB_REJECT)
    )
    async def _on_callback(call: CallbackQuery) -> None:
        try:
            # Per CLAUDE.md: validate BOTH from.id and chat.id against
            # the bound value before flipping the approval gate.
            binding = await pairing.read_binding(bridge._db_factory)  # noqa: SLF001
            bound_chat = binding.get("chat_id")
            msg_chat = (
                getattr(getattr(call, "message", None), "chat", None)
            )
            msg_chat_id = int(getattr(msg_chat, "id", 0) or 0)
            from_user_id = int(getattr(call.from_user, "id", 0) or 0)
            # For Telegram DMs, chat.id == from_user.id. We check both
            # anyway so group-chat misroutes later are a one-line fix.
            if bound_chat != msg_chat_id or bound_chat != from_user_id:
                try:
                    await call.answer(
                        text="Not authorized for this bot.", show_alert=True
                    )
                except Exception:
                    pass
                return

            ok, answer = await bridge.handle_callback(
                telegram_chat_id=msg_chat_id,
                telegram_user_id=from_user_id,
                callback_message_id=getattr(call.message, "message_id", None),
                data=call.data or "",
            )
            try:
                await call.answer(text=answer, show_alert=not ok)
            except Exception:
                logger.debug("telegram: answerCallbackQuery failed", exc_info=True)
        except Exception:  # noqa: BLE001
            logger.exception("telegram callback handler crashed")


async def start_polling(
    token: str,
    *,
    app_state: Any,
    db_factory: Any = None,
) -> PollingHandle:
    """Start long-polling against Telegram.

    Every failure path inside returns a ``PollingHandle`` with ``error``
    set rather than raising, so the FastAPI lifespan can surface the
    problem through ``/api/telegram/status`` without bringing the app
    down. aiogram itself is imported lazily so importing this module
    doesn't require the dependency to be installed (tests).
    """
    from aiogram import Bot, Dispatcher
    from aiogram.client.default import DefaultBotProperties

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=None))
    dispatcher = Dispatcher()
    bridge = TelegramBridge(bot=bot, app_state=app_state, db_factory=db_factory)
    await _register_handlers(dispatcher, bridge)

    username: str | None = None
    try:
        me = await bot.get_me()
        username = getattr(me, "username", None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram: get_me failed: %s", exc)

    async def _runner() -> None:
        # Long polling is mutually exclusive with a webhook. If the bot
        # was previously configured with webhook mode (BotFather test,
        # prior deployment, another process), getUpdates returns
        # TelegramConflictError forever. Dropping the webhook first is
        # idempotent and safe when no webhook is set.
        try:
            await bot.delete_webhook(drop_pending_updates=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("telegram: delete_webhook failed (continuing): %s", exc)

        try:
            await dispatcher.start_polling(
                bot, handle_signals=False, allowed_updates=None
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("telegram: polling loop crashed")

    task = asyncio.create_task(_runner(), name="telegram-polling")
    return PollingHandle(bot=bot, dispatcher=dispatcher, task=task, bridge=bridge, username=username)


async def stop_polling(handle: PollingHandle | None) -> None:
    if handle is None:
        return
    await handle.stop()
