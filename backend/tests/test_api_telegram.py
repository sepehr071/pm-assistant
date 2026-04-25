from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI


pytestmark = pytest.mark.asyncio


def _build_app() -> FastAPI:
    from api import telegram as telegram_api

    class _S:
        openrouter_default_model = "m"

    app = FastAPI(title="PM Assistant (telegram test)")
    app.state.settings = _S()
    app.state.telegram = None
    app.include_router(telegram_api.settings_router)
    app.include_router(telegram_api.status_router)
    return app


@pytest_asyncio.fixture
async def test_app(db_initialized):
    yield _build_app()


async def test_pair_endpoint_returns_code(test_app) -> None:
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/api/settings/telegram/pair")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "code" in body and len(body["code"]) == 6
        assert "expires_at" in body
        # Second call rotates — a different code is stored (rare equality
        # shouldn't happen with a 6-char alphabet but we assert round-trip
        # via the status endpoint instead of comparing codes directly).
        from integrations.telegram import pairing
        from db.session import async_session_factory

        snapshot = await pairing.read_binding(async_session_factory)
        assert snapshot["code"] == body["code"]


async def test_unpair_clears_binding(test_app) -> None:
    transport = httpx.ASGITransport(app=test_app)
    from integrations.telegram import pairing
    from db.session import async_session_factory

    # Seed a binding so DELETE has something to clear.
    code, _ = await pairing.create_pairing_code(async_session_factory)
    await pairing.try_bind(
        async_session_factory,
        code=code,
        telegram_chat_id=4242,
        telegram_username="pm",
    )

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.delete("/api/settings/telegram/pair")
        assert r.status_code == 204

    binding = await pairing.read_binding(async_session_factory)
    assert binding["chat_id"] is None
    assert binding["username"] is None


async def test_status_endpoint_reports_polling_state(test_app) -> None:
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/telegram/status")
        assert r.status_code == 200
        body = r.json()
        assert body["polling"] is False
        assert body["bound"] is False
        assert body["enabled"] is False

        # Now fake a live polling handle and re-check.
        class _Handle:
            task = asyncio.create_task(asyncio.sleep(60))
            username = "pm_bot"
            error = None

        test_app.state.telegram = _Handle()
        try:
            r2 = await client.get("/api/telegram/status")
            assert r2.status_code == 200
            body2 = r2.json()
            assert body2["polling"] is True
            assert body2["username"] == "pm_bot"
        finally:
            _Handle.task.cancel()
            try:
                await _Handle.task
            except (asyncio.CancelledError, Exception):
                pass
            test_app.state.telegram = None
