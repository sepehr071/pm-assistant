from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from tests.conftest import (
    FakeMCPManager,
    FakeOpenRouter,
    stop_chunk,
    token_chunk,
    tool_call_chunk,
)


def _build_app(fake_llm: FakeOpenRouter, fake_mcp: FakeMCPManager) -> FastAPI:
    from api import chats, stream, tools
    from api import settings as settings_router

    class _S:
        openrouter_default_model = "anthropic/claude-sonnet-4.6"

    # Set state eagerly so httpx.ASGITransport works without a lifespan runner.
    app = FastAPI(title="PM Assistant (test)")
    app.state.settings = _S()
    app.state.llm = fake_llm
    app.state.mcp = fake_mcp
    app.include_router(chats.router)
    app.include_router(stream.router)
    app.include_router(tools.router)
    app.include_router(settings_router.router)
    return app


@pytest_asyncio.fixture
async def app_with_fakes(db_initialized, fake_llm_factory, fake_mcp_factory):
    from api import stream as stream_module

    stream_module._reset_sessions()

    fake_llm: FakeOpenRouter = fake_llm_factory()
    fake_mcp: FakeMCPManager = fake_mcp_factory()

    app = _build_app(fake_llm, fake_mcp)

    yield app, fake_llm, fake_mcp

    stream_module._reset_sessions()


def _parse_sse_block(block: str) -> dict[str, Any] | None:
    """Parse one SSE 'block' (up to a blank line) into {event, data}."""
    event: str | None = None
    data_lines: list[str] = []
    for line in block.splitlines():
        if not line or line.startswith(":"):
            continue
        if line.startswith("event:"):
            event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:") :].lstrip())
    if not data_lines and event is None:
        return None
    try:
        payload: Any = json.loads("\n".join(data_lines))
    except json.JSONDecodeError:
        payload = {"raw": "\n".join(data_lines)}
    return {"event": event or "message", "data": payload}


async def _read_sse_stream(
    response: httpx.Response, stop_event: asyncio.Event, events: list[dict[str, Any]]
) -> None:
    buffer = ""
    async for chunk in response.aiter_text():
        buffer += chunk
        while "\n\n" in buffer:
            raw, buffer = buffer.split("\n\n", 1)
            parsed = _parse_sse_block(raw)
            if parsed is not None:
                events.append(parsed)
                if parsed["event"] in ("done", "error"):
                    stop_event.set()
                    return


@pytest.mark.asyncio
async def test_post_message_streams_tokens_and_done(app_with_fakes):
    app, fake_llm, fake_mcp = app_with_fakes
    fake_llm.script.append(
        [
            token_chunk("Hi"),
            token_chunk(" there"),
            stop_chunk("stop"),
        ]
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/chats", json={"title": "Test"})
        assert resp.status_code == 201, resp.text
        chat_id = resp.json()["id"]

        events: list[dict[str, Any]] = []
        stop_event = asyncio.Event()
        async with client.stream(
            "POST",
            f"/api/chats/{chat_id}/messages",
            json={"text": "hello"},
        ) as r:
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/event-stream")
            await asyncio.wait_for(_read_sse_stream(r, stop_event, events), timeout=10.0)

    types = [e["event"] for e in events]
    assert "token" in types
    assert types[-1] == "done"


@pytest.mark.asyncio
async def test_sse_emits_read_tool_call_result(app_with_fakes):
    """End-to-end SSE roundtrip with a read (auto-approved) tool call.

    The httpx/Starlette test transports buffer the full response before
    returning, so we can only test streams that *complete* without waiting
    for an external event. The approval gate itself is covered by the
    AgentSession unit tests.
    """
    app, fake_llm, fake_mcp = app_with_fakes

    fake_llm.script.append(
        [
            tool_call_chunk(
                index=0,
                id="call_r",
                name="jira__search_issues",
                args_piece='{"jql":"me"}',
            ),
            stop_chunk("tool_calls"),
        ]
    )
    fake_llm.script.append(
        [
            token_chunk("Found 2 issues"),
            stop_chunk("stop"),
        ]
    )
    fake_mcp.responses["jira__search_issues"] = {
        "content": "2 issues",
        "is_error": False,
        "structured": None,
    }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/chats", json={"title": "Read test"})
        assert resp.status_code == 201
        chat_id = resp.json()["id"]

        events: list[dict[str, Any]] = []
        stop_event = asyncio.Event()
        async with client.stream(
            "POST",
            f"/api/chats/{chat_id}/messages",
            json={"text": "find stuff"},
        ) as r:
            assert r.status_code == 200
            await asyncio.wait_for(_read_sse_stream(r, stop_event, events), timeout=10.0)

    types = [e["event"] for e in events]
    # No tool_call_request (read is auto-approved).
    assert "tool_call_request" not in types
    assert "tool_call_result" in types
    assert "token" in types
    assert types[-1] == "done"

    result_evt = next(e for e in events if e["event"] == "tool_call_result")
    assert result_evt["data"]["result"] == "2 issues"
    assert fake_mcp.calls == [("jira__search_issues", {"jql": "me"})]


@pytest.mark.asyncio
async def test_approve_endpoint_sets_pending_event(app_with_fakes):
    """Unit-level check that POST /approve reaches the in-process session.

    We inject a session with a pending approval, then hit the endpoint and
    verify the pending approval flipped.
    """
    app, _, fake_mcp = app_with_fakes

    from agent.loop import AgentSession, PendingApproval
    from api import stream as stream_module
    from db.session import async_session_factory

    # Create a chat row so the endpoint accepts the chat_id.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/chats", json={"title": "Approval unit test"})
        assert resp.status_code == 201
        chat_id = resp.json()["id"]

        class _S:
            openrouter_default_model = "anthropic/claude-sonnet-4.6"

        session = AgentSession(
            chat_id=chat_id,
            llm=app.state.llm,
            mcp=fake_mcp,
            settings=_S(),
            auto_approve=set(),
            db_factory=async_session_factory,
            default_model="m",
        )
        pending = PendingApproval(tool_call_id="tc_42")
        session.pending["tc_42"] = pending
        stream_module._sessions[chat_id] = session

        approve = await client.post(
            f"/api/chats/{chat_id}/approve",
            json={"tool_call_id": "tc_42", "approved": True},
        )
        assert approve.status_code == 204, approve.text
        assert pending.event.is_set()
        assert pending.approved is True

        # Second call is a 404 because we consumed the pending approval
        stream_module._sessions[chat_id].pending.pop("tc_42", None)
        second = await client.post(
            f"/api/chats/{chat_id}/approve",
            json={"tool_call_id": "tc_42", "approved": True},
        )
        assert second.status_code == 404


@pytest.mark.asyncio
async def test_approve_endpoint_errors(app_with_fakes):
    app, _, _ = app_with_fakes

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/chats/999/approve",
            json={"tool_call_id": "x", "approved": True},
        )
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_chats_crud_endpoints(app_with_fakes):
    app, _, _ = app_with_fakes

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/api/chats", json={})
        assert r.status_code == 201
        chat = r.json()
        assert chat["title"] == "New chat"
        chat_id = chat["id"]

        r = await client.get("/api/chats")
        assert r.status_code == 200
        ids = [c["id"] for c in r.json()]
        assert chat_id in ids

        r = await client.patch(f"/api/chats/{chat_id}", json={"title": "Renamed"})
        assert r.status_code == 200
        assert r.json()["title"] == "Renamed"

        r = await client.get(f"/api/chats/{chat_id}/messages")
        assert r.status_code == 200
        assert r.json() == []

        r = await client.delete(f"/api/chats/{chat_id}")
        assert r.status_code == 204

        r = await client.get(f"/api/chats/{chat_id}/messages")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_settings_endpoints_roundtrip(app_with_fakes):
    app, _, _ = app_with_fakes

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/settings")
        assert r.status_code == 200
        body = r.json()
        assert body["default_model"] == "anthropic/claude-sonnet-4.6"
        assert body["system_prompt"] == ""
        assert body["auto_approve_tools"] == []

        r = await client.patch(
            "/api/settings",
            json={
                "default_model": "openai/gpt-4o-mini",
                "system_prompt": "you are helpful",
                "auto_approve_tools": ["jira__search_issues"],
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["default_model"] == "openai/gpt-4o-mini"
        assert body["system_prompt"] == "you are helpful"
        assert body["auto_approve_tools"] == ["jira__search_issues"]

        r = await client.get("/api/settings")
        assert r.status_code == 200
        assert r.json() == body


@pytest.mark.asyncio
async def test_tools_and_mcp_status(app_with_fakes):
    app, _, fake_mcp = app_with_fakes

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/tools")
        assert r.status_code == 200
        body = r.json()
        assert "servers" in body
        assert "jira" in body["servers"]
        tool_names = [t["name"] for t in body["servers"]["jira"]["tools"]]
        assert "search_issues" in tool_names
        assert "create_issue" in tool_names

        r = await client.get("/api/mcp/status")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        assert body[0]["name"] == "jira"
        assert body[0]["healthy"] is True
