from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from integrations.telegram import pairing


pytestmark = pytest.mark.asyncio


async def test_generate_code_is_6_alphanumeric() -> None:
    for _ in range(50):
        code = pairing.generate_code()
        assert len(code) == 6
        assert code.isalnum()
        # All chars come from the curated alphabet.
        for ch in code:
            assert ch in pairing.PAIRING_ALPHABET


async def test_code_excludes_ambiguous_chars() -> None:
    for _ in range(200):
        code = pairing.generate_code()
        # These glyphs confuse users on mobile — must never appear.
        for banned in "0OI1":
            assert banned not in code


async def test_code_expires_after_ttl(db_initialized) -> None:
    db_factory = db_initialized
    code, expires_at = await pairing.create_pairing_code(db_factory, ttl_seconds=60)
    # Expiry stored as UTC ISO string; make sure it round-trips properly.
    binding = await pairing.read_binding(db_factory)
    assert binding["code"] == code
    assert binding["expires_at"] is not None

    # Force-expire by passing a "now" after the stored expiry.
    future = expires_at + timedelta(seconds=5)
    ok, reason = await pairing.try_bind(
        db_factory,
        code=code,
        telegram_chat_id=4242,
        telegram_username="pm",
        now=future,
    )
    assert ok is False
    assert reason == "code_expired"
    # Expired code should be swept out so the user regenerates cleanly.
    after = await pairing.read_binding(db_factory)
    assert after["code"] is None
    assert after["expires_at"] is None


async def test_bind_stores_chat_id(db_initialized) -> None:
    db_factory = db_initialized
    code, _ = await pairing.create_pairing_code(db_factory)
    ok, reason = await pairing.try_bind(
        db_factory,
        code=code,
        telegram_chat_id=987654321,
        telegram_username="pm_lead",
    )
    assert ok is True, reason
    binding = await pairing.read_binding(db_factory)
    assert binding["chat_id"] == 987654321
    assert binding["username"] == "pm_lead"


async def test_bind_consumes_code(db_initialized) -> None:
    db_factory = db_initialized
    code, _ = await pairing.create_pairing_code(db_factory)
    ok, _ = await pairing.try_bind(
        db_factory,
        code=code,
        telegram_chat_id=111,
        telegram_username=None,
    )
    assert ok is True
    # Second bind attempt with the same code must fail — code is gone.
    ok2, reason2 = await pairing.try_bind(
        db_factory,
        code=code,
        telegram_chat_id=222,
        telegram_username=None,
    )
    assert ok2 is False
    assert reason2 == "no_pending_code"


async def test_unpair_clears_binding_and_code(db_initialized) -> None:
    db_factory = db_initialized
    code, _ = await pairing.create_pairing_code(db_factory)
    await pairing.try_bind(
        db_factory,
        code=code,
        telegram_chat_id=333,
        telegram_username="x",
    )
    # Also queue up a fresh code to prove unpair wipes that too.
    await pairing.create_pairing_code(db_factory)
    await pairing.unpair(db_factory)

    binding = await pairing.read_binding(db_factory)
    assert binding["chat_id"] is None
    assert binding["username"] is None
    assert binding["code"] is None
    assert binding["expires_at"] is None


async def test_mismatch_reports_code_mismatch(db_initialized) -> None:
    db_factory = db_initialized
    await pairing.create_pairing_code(db_factory)
    ok, reason = await pairing.try_bind(
        db_factory,
        code="ZZZZZZ",
        telegram_chat_id=1,
        telegram_username=None,
    )
    assert ok is False
    assert reason == "code_mismatch"
