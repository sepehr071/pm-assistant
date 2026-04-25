"""HTTP endpoints for the Telegram gateway.

Two surfaces:

* ``/api/settings/telegram/*`` — pairing-code lifecycle. Produces a
  fresh code the user pastes into the bot and clears the binding on
  unpair.
* ``/api/telegram/status`` — health probe for the Settings page so the
  frontend can show a green/amber/red dot next to "Telegram gateway".

The pairing module owns all persistence — these handlers are thin
wrappers that return pydantic models.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request, status
from pydantic import BaseModel

from db.session import async_session_factory
from integrations.telegram import pairing


# Router prefixes:
#   POST/DELETE /api/settings/telegram/pair
#   GET        /api/telegram/status
# Two routers share this module because they logically belong to the
# same feature; FastAPI doesn't mind a single file mounting more than
# one APIRouter.
settings_router = APIRouter(
    prefix="/api/settings/telegram", tags=["telegram-settings"]
)
status_router = APIRouter(prefix="/api/telegram", tags=["telegram"])


class PairingCodeOut(BaseModel):
    code: str
    expires_at: datetime


class TelegramStatusOut(BaseModel):
    polling: bool
    bound: bool
    enabled: bool
    username: str | None = None
    error: str | None = None


@settings_router.post("/pair", response_model=PairingCodeOut)
async def create_pairing_code_endpoint() -> PairingCodeOut:
    code, expires_at = await pairing.create_pairing_code(async_session_factory)
    return PairingCodeOut(code=code, expires_at=expires_at)


@settings_router.delete("/pair", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pairing_binding() -> None:
    await pairing.unpair(async_session_factory)
    return None


@status_router.get("/status", response_model=TelegramStatusOut)
async def get_telegram_status(request: Request) -> TelegramStatusOut:
    binding = await pairing.read_binding(async_session_factory)
    enabled = bool(binding.get("enabled"))
    bound_chat = binding.get("chat_id")
    username = binding.get("username") if isinstance(binding.get("username"), str) else None

    handle: Any = getattr(request.app.state, "telegram", None)
    polling = False
    error: str | None = None
    if handle is not None:
        task = getattr(handle, "task", None)
        polling = bool(task is not None and not task.done())
        error = getattr(handle, "error", None)
        # Prefer the bot's resolved @username over the stored one when
        # we have it; the stored value is whatever the paired user's
        # handle was at bind time and may be stale.
        if getattr(handle, "username", None):
            username = handle.username

    return TelegramStatusOut(
        polling=polling,
        bound=bound_chat is not None,
        enabled=enabled,
        username=username,
        error=error,
    )
