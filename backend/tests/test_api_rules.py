"""Tests for /api/rules CRUD endpoints + scheduler reload hook.

Strategy:
- Build a minimal FastAPI app with only the chats + rules routers so we
  don't depend on Agent B's scheduler module being importable.
- Inject a fake scheduler as `app.state.scheduler` to assert `reload()` is
  called on mutations.
- Monkeypatch `services.rule_compiler.compile_rule` with a deterministic
  fake that returns a canned `CompiledSpec` (or raises on cue), so these
  tests never call the real LLM.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from services.rule_compiler import (
    CompiledFilter,
    CompiledSpec,
    RuleCompilationError,
)


# ---------------------------------------------------------------------------
# Fixtures / fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeScheduler:
    reload_calls: int = 0
    reload_exc: Exception | None = None

    async def reload(self) -> None:
        self.reload_calls += 1
        if self.reload_exc is not None:
            raise self.reload_exc

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


@dataclass
class _FakeSettings:
    openrouter_default_model: str = "anthropic/claude-sonnet-4.6"


class _FakeLLM:
    """Placeholder — compile_rule is monkeypatched so this is never called."""

    async def chat(self, **_: Any) -> Any:
        raise AssertionError("FakeLLM.chat should be patched out")


class _FakeMcp:
    def list_all_tools(self) -> list[dict[str, Any]]:
        return []


def _canonical_spec() -> CompiledSpec:
    return CompiledSpec(
        source_tool="slack__conversations_history",
        source_args={"channel": "D0123", "limit": 20},
        filter=CompiledFilter(
            kind="message_from_user_contains",
            user="Sepehr",
            contains=["roadmap"],
        ),
        action_prompt="Reply: on it.",
    )


def _build_app(scheduler: FakeScheduler) -> FastAPI:
    from api import rules as rules_router

    app = FastAPI(title="rules-test")
    app.state.settings = _FakeSettings()
    app.state.llm = _FakeLLM()
    app.state.mcp = _FakeMcp()
    app.state.scheduler = scheduler
    app.include_router(rules_router.router)
    return app


@pytest_asyncio.fixture
async def client_and_scheduler(db_initialized, monkeypatch):
    """Yield (client, scheduler) with a deterministic canned compiler.

    `db_initialized` resets the in-memory SQLite between tests.
    """
    scheduler = FakeScheduler()
    app = _build_app(scheduler)

    # Default: every compile succeeds with the canonical spec.
    async def _fake_compile(description: str, mcp: Any, llm: Any, model: str | None = None):
        return _canonical_spec()

    monkeypatch.setattr("api.rules.compile_rule", _fake_compile)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, scheduler, monkeypatch


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_post_rules_happy_path_reloads_scheduler(client_and_scheduler) -> None:
    client, scheduler, _ = client_and_scheduler

    r = await client.post(
        "/api/rules",
        json={
            "description": "DM me when Sepehr says roadmap",
            "interval_seconds": 120,
            "auto_approve": False,
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()

    assert body["description"] == "DM me when Sepehr says roadmap"
    assert body["interval_seconds"] == 120
    assert body["auto_approve"] is False
    assert body["enabled"] is True
    assert body["recent_firings"] == []
    # Track A: POST allocates + links a per-rule activity chat.
    assert body["activity_conversation_id"] is not None
    # compiled_spec round-trip
    spec = body["compiled_spec"]
    assert spec["source_tool"] == "slack__conversations_history"
    assert spec["filter"]["kind"] == "message_from_user_contains"
    assert spec["filter"]["user"] == "Sepehr"
    assert spec["filter"]["contains"] == ["roadmap"]
    assert spec["action_prompt"] == "Reply: on it."

    assert scheduler.reload_calls == 1


async def test_post_rules_compiler_refuses_returns_422(client_and_scheduler) -> None:
    client, scheduler, monkeypatch = client_and_scheduler

    async def _refuse(description: str, mcp: Any, llm: Any, model: str | None = None):
        raise RuleCompilationError("description is too vague")

    monkeypatch.setattr("api.rules.compile_rule", _refuse)

    r = await client.post(
        "/api/rules",
        json={"description": "do the thing", "interval_seconds": 120},
    )
    assert r.status_code == 422, r.text
    assert "vague" in r.json()["detail"]
    assert scheduler.reload_calls == 0


async def test_post_rules_rejects_interval_below_minimum(client_and_scheduler) -> None:
    client, scheduler, _ = client_and_scheduler

    r = await client.post(
        "/api/rules",
        json={
            "description": "valid nl",
            "interval_seconds": 10,
            "auto_approve": False,
        },
    )
    assert r.status_code == 422, r.text
    # pydantic reports field validation — scheduler never touched.
    assert scheduler.reload_calls == 0


async def test_get_rules_orders_desc_and_carries_recent_firings(client_and_scheduler) -> None:
    client, _, _ = client_and_scheduler

    # Create two rules.
    r1 = await client.post(
        "/api/rules",
        json={"description": "first", "interval_seconds": 60},
    )
    assert r1.status_code == 201
    id1 = r1.json()["id"]

    r2 = await client.post(
        "/api/rules",
        json={"description": "second", "interval_seconds": 300},
    )
    assert r2.status_code == 201
    id2 = r2.json()["id"]

    # Seed a firing against the first rule.
    from datetime import UTC, datetime

    from db.models import RuleFiring
    from db.session import async_session_factory

    async with async_session_factory() as s:
        s.add(
            RuleFiring(
                rule_id=id1,
                fired_at=datetime.now(UTC),
                status="matched",
                match_summary="hit",
            )
        )
        await s.commit()

    r = await client.get("/api/rules")
    assert r.status_code == 200
    body = r.json()

    # Second created → newer created_at → first in desc order.
    assert [row["id"] for row in body] == [id2, id1]

    # Rule 1 carries its firing; rule 2 has none.
    first_rule = next(row for row in body if row["id"] == id1)
    second_rule = next(row for row in body if row["id"] == id2)
    assert len(first_rule["recent_firings"]) == 1
    assert first_rule["recent_firings"][0]["status"] == "matched"
    assert first_rule["recent_firings"][0]["match_summary"] == "hit"
    assert second_rule["recent_firings"] == []

    # Each rule owns its own per-rule activity conversation (Track A).
    assert first_rule["activity_conversation_id"] is not None
    assert second_rule["activity_conversation_id"] is not None
    assert first_rule["activity_conversation_id"] != second_rule["activity_conversation_id"]


async def test_patch_rule_disables_and_reloads(client_and_scheduler) -> None:
    client, scheduler, _ = client_and_scheduler

    r = await client.post(
        "/api/rules", json={"description": "hi", "interval_seconds": 60}
    )
    assert r.status_code == 201
    rule_id = r.json()["id"]
    assert scheduler.reload_calls == 1

    patch = await client.patch(
        f"/api/rules/{rule_id}",
        json={"enabled": False},
    )
    assert patch.status_code == 200, patch.text
    body = patch.json()
    assert body["enabled"] is False
    assert scheduler.reload_calls == 2

    # DB row actually flipped.
    from db.models import Rule
    from db.session import async_session_factory

    async with async_session_factory() as s:
        row = await s.get(Rule, rule_id)
        assert row is not None
        assert row.enabled is False


async def test_patch_rule_rejects_invalid_compiled_spec(client_and_scheduler) -> None:
    client, _, _ = client_and_scheduler

    r = await client.post(
        "/api/rules", json={"description": "hi", "interval_seconds": 60}
    )
    rule_id = r.json()["id"]

    # Missing required `source_tool` → 422.
    patch = await client.patch(
        f"/api/rules/{rule_id}",
        json={"compiled_spec": {"source_args": {}, "filter": {"kind": "schedule_only"}}},
    )
    assert patch.status_code == 422


async def test_delete_rule_cascades_firings(client_and_scheduler) -> None:
    client, scheduler, _ = client_and_scheduler

    r = await client.post(
        "/api/rules", json={"description": "hi", "interval_seconds": 60}
    )
    rule_body = r.json()
    rule_id = rule_body["id"]
    activity_id = rule_body["activity_conversation_id"]
    assert activity_id is not None
    initial_reloads = scheduler.reload_calls

    # Seed a firing and a message in the activity chat so we can confirm
    # both cascade on delete.
    from datetime import UTC, datetime

    from db.models import Conversation, Message, RuleFiring
    from db.session import async_session_factory

    async with async_session_factory() as s:
        s.add(
            RuleFiring(
                rule_id=rule_id,
                fired_at=datetime.now(UTC),
                status="matched",
                match_summary="x",
                conversation_id=activity_id,
            )
        )
        s.add(
            Message(
                conversation_id=activity_id,
                role="user",
                content="[rule:hi]",
            )
        )
        await s.commit()

    delete_resp = await client.delete(f"/api/rules/{rule_id}")
    assert delete_resp.status_code == 204
    assert scheduler.reload_calls == initial_reloads + 1

    # subsequent GET doesn't return it
    listing = await client.get("/api/rules")
    assert listing.status_code == 200
    assert all(row["id"] != rule_id for row in listing.json())

    # firings also gone, plus the bound conversation and its messages.
    from sqlalchemy import select

    async with async_session_factory() as s:
        remaining_firings = (
            await s.execute(select(RuleFiring).where(RuleFiring.rule_id == rule_id))
        ).scalars().all()
        assert remaining_firings == []

        convo = await s.get(Conversation, activity_id)
        assert convo is None

        remaining_messages = (
            await s.execute(
                select(Message).where(Message.conversation_id == activity_id)
            )
        ).scalars().all()
        assert remaining_messages == []


async def test_run_now_dispatches_to_engine(client_and_scheduler, monkeypatch) -> None:
    """`POST /api/rules/{id}/run-now` calls `services.rule_engine.tick` directly."""
    client, _, _ = client_and_scheduler

    r = await client.post(
        "/api/rules", json={"description": "hi", "interval_seconds": 60}
    )
    rule_id = r.json()["id"]

    # Stub out rule_engine module with a fake tick that returns a RuleFiring.
    import sys
    import types
    from datetime import UTC, datetime

    from db.models import RuleFiring
    from db.session import async_session_factory

    captured: dict[str, Any] = {}

    async def _fake_tick(
        rid: int,
        *,
        mcp: Any,
        llm: Any,
        settings: Any,
        db_factory: Any,
        default_model: str,
    ) -> RuleFiring:
        captured["rid"] = rid
        captured["default_model"] = default_model
        captured["db_factory_is_session_factory"] = db_factory is async_session_factory
        async with async_session_factory() as s:
            firing = RuleFiring(
                rule_id=rid,
                fired_at=datetime.now(UTC),
                status="matched",
                match_summary="ran via run-now",
            )
            s.add(firing)
            await s.commit()
            await s.refresh(firing)
        return firing

    fake_module = types.ModuleType("services.rule_engine")
    fake_module.tick = _fake_tick  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "services.rule_engine", fake_module)
    # Also patch the attribute on the `services` package, because once
    # `services.rule_engine` has been imported earlier in the test session,
    # `from services import rule_engine` resolves through the package
    # namespace and ignores our `sys.modules` swap.
    import services as _services_pkg  # noqa: WPS433

    monkeypatch.setattr(_services_pkg, "rule_engine", fake_module, raising=False)

    resp = await client.post(f"/api/rules/{rule_id}/run-now")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rule_id"] == rule_id
    assert body["status"] == "matched"
    assert body["match_summary"] == "ran via run-now"

    assert captured["rid"] == rule_id
    assert captured["default_model"] == "anthropic/claude-sonnet-4.6"
    assert captured["db_factory_is_session_factory"] is True


# ---------------------------------------------------------------------------
# Track B — firings / digest / health
# ---------------------------------------------------------------------------


async def test_get_firings_honors_limit_and_status_filter(client_and_scheduler) -> None:
    client, _, _ = client_and_scheduler

    r = await client.post(
        "/api/rules", json={"description": "timeline", "interval_seconds": 60}
    )
    rule_id = r.json()["id"]
    activity_id = r.json()["activity_conversation_id"]

    # Seed a mix of firings.
    from datetime import UTC, datetime, timedelta

    from db.models import RuleFiring
    from db.session import async_session_factory

    now = datetime.now(UTC)
    rows = [
        RuleFiring(
            rule_id=rule_id,
            fired_at=now - timedelta(minutes=30),
            status="matched",
            match_summary="m1",
            conversation_id=activity_id,
            trigger_summary="m1",
            duration_ms=42,
        ),
        RuleFiring(
            rule_id=rule_id,
            fired_at=now - timedelta(minutes=25),
            status="error",
            error="e1",
            conversation_id=activity_id,
            trigger_summary="e1",
            duration_ms=12,
        ),
        RuleFiring(
            rule_id=rule_id,
            fired_at=now - timedelta(minutes=20),
            status="no_match",
            conversation_id=activity_id,
            trigger_summary="polled slack",
            duration_ms=5,
        ),
        RuleFiring(
            rule_id=rule_id,
            fired_at=now - timedelta(minutes=15),
            status="matched",
            match_summary="m2",
            conversation_id=activity_id,
            trigger_summary="m2",
            duration_ms=44,
        ),
    ]
    async with async_session_factory() as s:
        for row in rows:
            s.add(row)
        await s.commit()

    # Full list, newest-first.
    resp = await client.get(f"/api/rules/{rule_id}/firings")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 4
    assert body[0]["status"] == "matched"
    assert body[0]["trigger_summary"] == "m2"
    assert body[0]["conversation_id"] == activity_id
    assert body[0]["duration_ms"] == 44

    # limit=2 truncates.
    resp = await client.get(f"/api/rules/{rule_id}/firings?limit=2")
    assert resp.status_code == 200
    assert len(resp.json()) == 2

    # status filter as CSV.
    resp = await client.get(
        f"/api/rules/{rule_id}/firings?status=matched,error"
    )
    assert resp.status_code == 200
    statuses = [row["status"] for row in resp.json()]
    assert set(statuses) == {"matched", "error"}
    assert len(statuses) == 3

    # 404 for unknown rule.
    resp = await client.get("/api/rules/99999/firings")
    assert resp.status_code == 404


async def test_get_digest_returns_24h_rollup_with_sparkline(client_and_scheduler) -> None:
    client, _, _ = client_and_scheduler

    r = await client.post(
        "/api/rules", json={"description": "digest", "interval_seconds": 60}
    )
    rule_id = r.json()["id"]
    activity_id = r.json()["activity_conversation_id"]

    from datetime import UTC, datetime, timedelta

    from db.models import RuleFiring
    from db.session import async_session_factory

    now = datetime.now(UTC)
    rows = [
        # inside window
        RuleFiring(
            rule_id=rule_id,
            fired_at=now - timedelta(minutes=30),
            status="matched",
            match_summary="m1",
            conversation_id=activity_id,
        ),
        RuleFiring(
            rule_id=rule_id,
            fired_at=now - timedelta(hours=5),
            status="matched",
            match_summary="m2",
            conversation_id=activity_id,
        ),
        RuleFiring(
            rule_id=rule_id,
            fired_at=now - timedelta(hours=2),
            status="error",
            error="kaboom\nmore",
            conversation_id=activity_id,
        ),
        RuleFiring(
            rule_id=rule_id,
            fired_at=now - timedelta(minutes=10),
            status="no_match",
            conversation_id=activity_id,
        ),
        RuleFiring(
            rule_id=rule_id,
            fired_at=now - timedelta(minutes=5),
            status="no_match",
            conversation_id=activity_id,
        ),
        # outside window (>24h ago)
        RuleFiring(
            rule_id=rule_id,
            fired_at=now - timedelta(hours=48),
            status="matched",
            match_summary="old",
            conversation_id=activity_id,
        ),
    ]
    async with async_session_factory() as s:
        for row in rows:
            s.add(row)
        await s.commit()

    resp = await client.get(f"/api/rules/{rule_id}/digest?window=24h")
    assert resp.status_code == 200
    body = resp.json()
    assert body["window"] == "24h"
    assert body["matched"] == 2
    assert body["no_match"] == 2
    assert body["error"] == 1
    assert body["first_match_at"] is not None
    assert body["last_error_at"] is not None
    assert body["last_error_text"] == "kaboom\nmore"
    assert isinstance(body["sparkline"], list)
    assert len(body["sparkline"]) == 24
    # Matched+error total within window == 3.
    assert sum(body["sparkline"]) == 3

    # 404 for unknown rule.
    resp = await client.get("/api/rules/99999/digest")
    assert resp.status_code == 404


async def test_list_rule_health_includes_consecutive_failures(
    client_and_scheduler,
) -> None:
    client, _, _ = client_and_scheduler

    # Seed two rules.
    r1 = await client.post(
        "/api/rules", json={"description": "healthy", "interval_seconds": 60}
    )
    r2 = await client.post(
        "/api/rules", json={"description": "struggling", "interval_seconds": 120}
    )
    id1 = r1.json()["id"]
    id2 = r2.json()["id"]

    # Prime the consecutive-error counter for id2 without going through tick().
    from services import rule_engine

    rule_engine.reset_error_counters()
    rule_engine._consecutive_errors[id2] = 3  # type: ignore[attr-defined]

    resp = await client.get("/api/rules/health")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 2
    by_rule = {item["rule"]["id"]: item for item in body}

    assert by_rule[id1]["consecutive_failures"] == 0
    assert by_rule[id2]["consecutive_failures"] == 3

    for item in body:
        digest = item["digest_24h"]
        assert digest["window"] == "24h"
        assert len(digest["sparkline"]) == 24
        assert item["rule"]["activity_conversation_id"] is not None

    rule_engine.reset_error_counters()
