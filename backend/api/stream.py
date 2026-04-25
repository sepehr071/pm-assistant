from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent.loop import AgentSession
from db.models import Conversation
from db.session import async_session_factory

router = APIRouter(prefix="/api/chats", tags=["stream"])


class MessageBody(BaseModel):
    text: str


class ApproveBody(BaseModel):
    tool_call_id: str
    approved: bool


# Module-level registry: one AgentSession per chat id.
_sessions: dict[int, AgentSession] = {}
_sessions_lock = asyncio.Lock()


async def build_agent_session(chat_id: int, app_state: Any) -> AgentSession:
    """Construct an AgentSession for a given conversation id.

    Module-level helper so both the HTTP POST /messages route and the
    Telegram bridge share identical session-construction semantics.
    Callers are responsible for registering the returned session in
    `_sessions` under lock if they want it discoverable by /approve.
    """
    settings = getattr(app_state, "settings", None)
    llm = getattr(app_state, "llm", None)
    mcp = getattr(app_state, "mcp", None)
    if settings is None or llm is None or mcp is None:
        raise HTTPException(status_code=503, detail="server not ready")

    # User settings (auto_approve, default_model, system_prompt) are
    # fetched eagerly so turns don't hit the DB each iteration.
    from agent.system_prompt import build_system_prompt  # local import avoids cycles
    from api.settings import read_settings_values  # local import avoids cycles

    values = await read_settings_values()
    default_model = values.get("default_model") or settings.openrouter_default_model
    auto_approve_list = values.get("auto_approve_tools") or []
    auto_approve = set(auto_approve_list) if isinstance(auto_approve_list, list) else set()
    yolo_mode = bool(values.get("yolo_mode"))

    # The stored "system_prompt" setting is treated as an *extension*
    # appended to the hardcoded base + live integration capabilities,
    # so the LLM always knows it's PM Assistant and what it can do.
    # Wrap the assembly in a builder so AgentSession can re-run it every
    # turn — picks up live integration state and settings changes
    # without forcing a session drop. When nothing changed the prompt
    # bytes stay identical, so prefix caching still hits.
    async def _builder() -> str:
        latest = await read_settings_values()
        extension = latest.get("system_prompt") or None
        live_yolo = bool(latest.get("yolo_mode"))
        return build_system_prompt(mcp, extension, yolo_mode=live_yolo)

    initial_prompt = build_system_prompt(mcp, values.get("system_prompt") or None, yolo_mode=yolo_mode)

    return AgentSession(
        chat_id=chat_id,
        llm=llm,
        mcp=mcp,
        settings=settings,
        auto_approve=auto_approve,
        db_factory=async_session_factory,
        default_model=default_model,
        system_prompt=initial_prompt,
        system_prompt_builder=_builder,
        yolo_mode=yolo_mode,
    )


async def _get_or_create_session(chat_id: int, request: Request) -> AgentSession:
    async with _sessions_lock:
        existing = _sessions.get(chat_id)
        if existing is not None:
            return existing
        session = await build_agent_session(chat_id, request.app.state)
        _sessions[chat_id] = session
        return session


def _drop_session(chat_id: int) -> None:
    session = _sessions.pop(chat_id, None)
    if session is not None:
        session.cancel_pending()


def _format_sse(event: str, data: dict[str, Any]) -> bytes:
    payload = json.dumps(data, default=str)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


@router.post("/{chat_id}/messages")
async def post_message(chat_id: int, body: MessageBody, request: Request) -> StreamingResponse:
    async with async_session_factory() as db_session:
        convo = await db_session.get(Conversation, chat_id)
        if convo is None:
            raise HTTPException(status_code=404, detail="chat not found")

    agent_session = await _get_or_create_session(chat_id, request)
    text = body.text

    async def event_gen():
        try:
            async for evt in agent_session.run_turn(text):
                etype = evt.get("type", "message")
                payload = {k: v for k, v in evt.items() if k != "type"}
                yield _format_sse(etype, payload)
        except asyncio.CancelledError:
            agent_session.cancel_pending()
            raise
        except Exception as exc:
            yield _format_sse("error", {"event_id": -1, "error": f"stream failed: {exc}"})

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{chat_id}/approve", status_code=status.HTTP_204_NO_CONTENT)
async def approve_tool_call(chat_id: int, body: ApproveBody) -> None:
    session = _sessions.get(chat_id)
    if session is None:
        raise HTTPException(status_code=404, detail="no active agent session for chat")
    ok = session.approve(body.tool_call_id, body.approved)
    if not ok:
        raise HTTPException(status_code=404, detail="no pending approval for that tool_call_id")
    return None


# Exposed for tests / lifespan teardown
def _reset_sessions() -> None:
    for sess in list(_sessions.values()):
        sess.cancel_pending()
    _sessions.clear()
