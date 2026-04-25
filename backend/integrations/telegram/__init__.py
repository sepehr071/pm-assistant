"""Telegram DM gateway for PM Assistant.

Public surface:

* :func:`start_polling` — spawn the long-polling task. Returns a
  ``PollingHandle`` whose ``.stop()`` method is idempotent. Never
  raises — failures end up on ``handle.error``.
* :func:`stop_polling` — shut the handle down cleanly.
* :func:`is_configured` — quick sanity check for
  ``TELEGRAM_BOT_TOKEN``.

Expected wiring (in ``backend/main.py`` lifespan, AFTER the scheduler
boot and rule-tool registration)::

    try:
        from integrations.telegram import start_polling, is_configured

        token = settings.telegram_bot_token
        values = await read_settings_values()  # from api.settings
        enabled = bool(values.get("telegram_enabled"))
        if is_configured(token) and enabled:
            app.state.telegram = await start_polling(
                token, app_state=app.state
            )
        else:
            app.state.telegram = None
    except Exception:
        logger.exception("telegram boot failed")
        app.state.telegram = None

Shutdown in the ``finally`` block::

    handle = getattr(app.state, "telegram", None)
    if handle is not None:
        try:
            await handle.stop()
        except Exception:
            pass
"""
from __future__ import annotations

from integrations.telegram.bot import (  # noqa: F401
    PollingHandle,
    is_configured,
    start_polling,
    stop_polling,
)

__all__ = [
    "PollingHandle",
    "is_configured",
    "start_polling",
    "stop_polling",
]
