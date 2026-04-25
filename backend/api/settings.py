from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import select

from db.models import UserSetting
from db.session import async_session_factory

router = APIRouter(prefix="/api/settings", tags=["settings"])


# Keys we expose via the settings API. Everything else in UserSetting is ignored here.
_ALLOWED_KEYS: tuple[str, ...] = (
    "default_model",
    "system_prompt",
    "auto_approve_tools",
    "yolo_mode",
    "show_tool_details",
    "telegram_enabled",
    "telegram_chat_id",
    "telegram_paired_username",
)


class SettingsOut(BaseModel):
    default_model: str
    system_prompt: str
    auto_approve_tools: list[str]
    yolo_mode: bool
    # Default off — non-technical users see a compact "called <tool>" pill
    # instead of the raw JSON args / result dump. Power users can toggle on.
    show_tool_details: bool = False
    telegram_enabled: bool = False
    telegram_chat_id: int | None = None
    telegram_paired_username: str | None = None


class SettingsPatch(BaseModel):
    default_model: str | None = None
    system_prompt: str | None = None
    auto_approve_tools: list[str] | None = None
    yolo_mode: bool | None = None
    show_tool_details: bool | None = None
    telegram_enabled: bool | None = None


def _parse_stored(value: str, key: str) -> Any:
    """Try JSON parse; fall back to the raw string."""
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value
    return parsed


async def read_settings_values() -> dict[str, Any]:
    """Return a dict of all persisted user settings (raw values).

    Falls back to empty dict if the DB isn't ready.
    """
    try:
        async with async_session_factory() as session:
            rows = (await session.execute(select(UserSetting))).scalars().all()
    except Exception:
        return {}
    out: dict[str, Any] = {}
    for row in rows:
        out[row.key] = _parse_stored(row.value, row.key)
    return out


def _defaults(request: Request | None) -> dict[str, Any]:
    default_model = ""
    if request is not None:
        settings_obj = getattr(request.app.state, "settings", None)
        default_model = getattr(settings_obj, "openrouter_default_model", "") or ""
    return {
        "default_model": default_model,
        "system_prompt": "",
        "auto_approve_tools": [],
        "yolo_mode": False,
    }


@router.get("", response_model=SettingsOut)
async def get_settings_endpoint(request: Request) -> SettingsOut:
    defaults = _defaults(request)
    values = await read_settings_values()

    default_model = values.get("default_model")
    if not isinstance(default_model, str) or not default_model:
        default_model = defaults["default_model"]

    system_prompt = values.get("system_prompt")
    if not isinstance(system_prompt, str):
        system_prompt = defaults["system_prompt"]

    auto_approve = values.get("auto_approve_tools")
    if not isinstance(auto_approve, list):
        auto_approve = defaults["auto_approve_tools"]
    else:
        auto_approve = [str(x) for x in auto_approve]

    yolo_mode = values.get("yolo_mode")
    if not isinstance(yolo_mode, bool):
        yolo_mode = defaults["yolo_mode"]

    show_tool_details = values.get("show_tool_details")
    if not isinstance(show_tool_details, bool):
        show_tool_details = False

    telegram_enabled = values.get("telegram_enabled")
    if not isinstance(telegram_enabled, bool):
        telegram_enabled = False

    tg_chat_raw = values.get("telegram_chat_id")
    telegram_chat_id = tg_chat_raw if isinstance(tg_chat_raw, int) else None

    tg_username_raw = values.get("telegram_paired_username")
    telegram_paired_username = (
        tg_username_raw if isinstance(tg_username_raw, str) else None
    )

    return SettingsOut(
        default_model=default_model,
        system_prompt=system_prompt,
        auto_approve_tools=auto_approve,
        yolo_mode=yolo_mode,
        show_tool_details=show_tool_details,
        telegram_enabled=telegram_enabled,
        telegram_chat_id=telegram_chat_id,
        telegram_paired_username=telegram_paired_username,
    )


@router.patch("", response_model=SettingsOut)
async def patch_settings(body: SettingsPatch, request: Request) -> SettingsOut:
    updates: dict[str, Any] = {}
    if body.default_model is not None:
        updates["default_model"] = body.default_model
    if body.system_prompt is not None:
        updates["system_prompt"] = body.system_prompt
    if body.auto_approve_tools is not None:
        updates["auto_approve_tools"] = list(body.auto_approve_tools)
    if body.yolo_mode is not None:
        updates["yolo_mode"] = bool(body.yolo_mode)
    if body.show_tool_details is not None:
        updates["show_tool_details"] = bool(body.show_tool_details)
    if body.telegram_enabled is not None:
        updates["telegram_enabled"] = bool(body.telegram_enabled)

    if updates:
        async with async_session_factory() as session:
            for key, raw_val in updates.items():
                if key not in _ALLOWED_KEYS:
                    continue
                value = json.dumps(raw_val) if not isinstance(raw_val, str) else raw_val
                existing = await session.get(UserSetting, key)
                if existing is None:
                    session.add(UserSetting(key=key, value=value))
                else:
                    existing.value = value
                    session.add(existing)
            await session.commit()

        # Drop cached agent sessions so new settings (yolo_mode, auto_approve,
        # system_prompt, default_model) take effect on the very next turn
        # rather than only for chats opened after this patch.
        from api.stream import _reset_sessions  # local import avoids cycles

        _reset_sessions()

        if "telegram_enabled" in updates:
            await _sync_telegram_polling(request, bool(updates["telegram_enabled"]))

    return await get_settings_endpoint(request)


async def _sync_telegram_polling(request: Request, enabled: bool) -> None:
    """Start or stop Telegram polling in response to a settings flip.

    Cold-starting on every API boot covers the normal case; this hot-path
    lets the user toggle the gateway from the UI without a restart.
    """
    app_state = request.app.state
    current = getattr(app_state, "telegram", None)

    if not enabled:
        if current is not None:
            try:
                await current.stop()
            except Exception:
                pass
            app_state.telegram = None
        return

    if current is not None:
        return

    settings_obj = getattr(app_state, "settings", None)
    token = getattr(settings_obj, "telegram_bot_token", None) if settings_obj else None
    if not token:
        return

    try:
        from integrations.telegram import is_configured, start_polling
    except Exception:
        return

    if not is_configured(token):
        return

    try:
        app_state.telegram = await start_polling(token, app_state=app_state)
    except Exception:
        app_state.telegram = None
