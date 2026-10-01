"""Pairing-code generation + binding helpers for the Telegram gateway.

Codes are 6 characters drawn from an alphanumeric alphabet with the
ambiguous characters ``0``, ``O``, ``I`` and ``1`` stripped out so they
read cleanly on a phone. A code is valid for ``PAIRING_TTL_SECONDS``
and is consumed on the first successful bind.

All state lives in the ``UserSetting`` key/value table — no new columns
needed. Stored keys are exported as module constants so other modules
(settings API, bridge, bot) don't duplicate string literals.
"""
from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.models import UserSetting


# ---- stored UserSetting keys -------------------------------------------------

KEY_ENABLED = "telegram_enabled"
KEY_PAIRING_CODE = "telegram_pairing_code"
KEY_PAIRING_EXPIRES_AT = "telegram_pairing_expires_at"
KEY_CHAT_ID = "telegram_chat_id"
KEY_PAIRED_USERNAME = "telegram_paired_username"

# ---- code shape --------------------------------------------------------------

PAIRING_CODE_LENGTH = 6
# Uppercase ASCII + digits minus ambiguous glyphs (0/O, 1/I).
PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PAIRING_TTL_SECONDS = 3600  # 1 hour


# ---- low-level UserSetting I/O ----------------------------------------------


def _encode(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value)


def _decode(raw: str | None) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


async def _get(session: AsyncSession, key: str) -> Any:
    row = await session.get(UserSetting, key)
    return _decode(row.value) if row else None


async def _set(session: AsyncSession, key: str, value: Any) -> None:
    encoded = _encode(value)
    row = await session.get(UserSetting, key)
    if row is None:
        session.add(UserSetting(key=key, value=encoded))
    else:
        row.value = encoded
        session.add(row)


async def _delete_keys(session: AsyncSession, keys: list[str]) -> None:
    if not keys:
        return
    await session.execute(delete(UserSetting).where(UserSetting.key.in_(keys)))


# ---- public API --------------------------------------------------------------


def generate_code(*, length: int = PAIRING_CODE_LENGTH) -> str:
    """Return a cryptographically random pairing code."""
    return "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(length))


async def create_pairing_code(
    db_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
    ttl_seconds: int = PAIRING_TTL_SECONDS,
) -> tuple[str, datetime]:
    """Generate a fresh pairing code, persist it, return (code, expires_at)."""
    moment = now or datetime.now(UTC)
    expires_at = moment + timedelta(seconds=ttl_seconds)
    code = generate_code()
    async with db_factory() as session:
        await _set(session, KEY_PAIRING_CODE, code)
        await _set(session, KEY_PAIRING_EXPIRES_AT, expires_at.isoformat())
        await session.commit()
    return code, expires_at


async def clear_pairing_code(db_factory: async_sessionmaker[AsyncSession]) -> None:
    async with db_factory() as session:
        await _delete_keys(session, [KEY_PAIRING_CODE, KEY_PAIRING_EXPIRES_AT])
        await session.commit()


async def read_binding(
    db_factory: async_sessionmaker[AsyncSession],
) -> dict[str, Any]:
    """Snapshot all Telegram-related UserSetting values."""
    async with db_factory() as session:
        rows = (await session.execute(select(UserSetting))).scalars().all()
    values: dict[str, Any] = {row.key: _decode(row.value) for row in rows}
    return {
        "enabled": bool(values.get(KEY_ENABLED)),
        "code": values.get(KEY_PAIRING_CODE),
        "expires_at": values.get(KEY_PAIRING_EXPIRES_AT),
        "chat_id": values.get(KEY_CHAT_ID),
        "username": values.get(KEY_PAIRED_USERNAME),
    }


async def set_enabled(
    db_factory: async_sessionmaker[AsyncSession], enabled: bool
) -> None:
    async with db_factory() as session:
        await _set(session, KEY_ENABLED, bool(enabled))
        await session.commit()


def _parse_expires(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=UTC)
    if isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    return None


async def try_bind(
    db_factory: async_sessionmaker[AsyncSession],
    *,
    code: str,
    telegram_chat_id: int,
    telegram_username: str | None,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Validate + consume a pairing code, then write the binding.

    Returns (ok, reason). `reason` is an empty string on success; otherwise
    a human-readable error for the bot to echo back to the user.
    """
    moment = now or datetime.now(UTC)
    async with db_factory() as session:
        stored_code = await _get(session, KEY_PAIRING_CODE)
        stored_exp_raw = await _get(session, KEY_PAIRING_EXPIRES_AT)
        if not isinstance(stored_code, str) or not stored_code:
            return False, "no_pending_code"

        # Constant-time-ish compare; codes are short and not secrets so
        # this is belt-and-suspenders more than strict necessity.
        if not secrets.compare_digest(stored_code.strip().upper(), code.strip().upper()):
            return False, "code_mismatch"

        expires_at = _parse_expires(stored_exp_raw)
        if expires_at is None or expires_at <= moment:
            # Code expired — clear so the user regenerates rather than
            # retrying a stale string forever.
            await _delete_keys(session, [KEY_PAIRING_CODE, KEY_PAIRING_EXPIRES_AT])
            await session.commit()
            return False, "code_expired"

        await _set(session, KEY_CHAT_ID, int(telegram_chat_id))
        if telegram_username:
            await _set(session, KEY_PAIRED_USERNAME, telegram_username)
        # Consume-on-use: code + expiry gone after a successful bind.
        await _delete_keys(session, [KEY_PAIRING_CODE, KEY_PAIRING_EXPIRES_AT])
        await session.commit()
        return True, ""


async def unpair(db_factory: async_sessionmaker[AsyncSession]) -> None:
    """Clear both the binding and any in-flight pairing code."""
    async with db_factory() as session:
        await _delete_keys(
            session,
            [
                KEY_PAIRING_CODE,
                KEY_PAIRING_EXPIRES_AT,
                KEY_CHAT_ID,
                KEY_PAIRED_USERNAME,
            ],
        )
        await session.commit()
