from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import desc, func, select

from agent.loop import AgentSession
from db.models import Conversation, Message, RuleFiring
from db.session import async_session_factory

log = logging.getLogger(__name__)

# Hardcoded title-generation model — small/fast, used only to summarise the
# first user message into a 3-6 word chat title. Independent of the user's
# default chat model. Reasoning is forced off to keep latency + cost minimal.
TITLE_MODEL = "x-ai/grok-4.1-fast"
TITLE_TIMEOUT_SECONDS = 15.0
TITLE_MAX_LEN = 80

# A forgotten approval modal must not pin the AgentSession forever. After
# this many seconds with no /approve call, the gate resolves as a rejection,
# feeds "User rejected this action." back to the LLM, and the turn continues.
APPROVAL_TIMEOUT_SECONDS = 1800.0

router = APIRouter(prefix="/api/chats", tags=["stream"])


class MessageBody(BaseModel):
    text: str


class ApproveBody(BaseModel):
    tool_call_id: str
    approved: bool


# Module-level registry: one AgentSession per chat id.
_sessions: dict[int, AgentSession] = {}
_sessions_lock = asyncio.Lock()

# Per-chat turn lock: serialize streaming turns for a single chat so two
# concurrent POSTs can't interleave messages / pending approvals / event
# counters on the shared mutable AgentSession and corrupt the transcript.
# Acquisition is non-blocking (see post_message): a contended lock yields a
# 409 instead of queueing. Mirrors the per-conversation locks the Telegram
# bridge already uses (integrations/telegram/bridge.py::_turn_locks).
_turn_locks: dict[int, asyncio.Lock] = {}


def _turn_lock_for(chat_id: int) -> asyncio.Lock:
    """Return the per-chat turn lock, creating it on first use.

    Safe without an outer mutex: dict.setdefault is atomic across the single
    asyncio event loop (no await between read and write).
    """
    lock = _turn_locks.get(chat_id)
    if lock is None:
        lock = asyncio.Lock()
        _turn_locks[chat_id] = lock
    return lock


def _turn_locked(chat_id: int) -> bool:
    """Whether a turn is currently in flight for this chat."""
    lock = _turn_locks.get(chat_id)
    return lock is not None and lock.locked()


def _release_turn_lock(chat_id: int) -> None:
    """Release the per-chat turn lock and drop it from the registry.

    The holder calls this in the stream's `finally`. We pop the entry when it
    is no longer held so the dict can't grow unbounded with one entry per
    chat that ever streamed. A 409 caller never creates a fresh entry (it
    only ever reads an already-held lock), so popping here is leak-free.
    """
    lock = _turn_locks.get(chat_id)
    if lock is None:
        return
    if lock.locked():
        lock.release()
    # Only drop the entry if nothing is now holding or waiting on it.
    if not lock.locked():
        _turn_locks.pop(chat_id, None)


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

    # User settings (auto_approve, system_prompt, …) are fetched eagerly
    # so turns don't hit the DB each iteration.
    from agent.system_prompt import build_system_prompt  # local import avoids cycles
    from api.settings import read_settings_values  # local import avoids cycles

    values = await read_settings_values()
    # Model is server-controlled (env var); user can no longer override it.
    default_model = settings.openrouter_default_model
    auto_approve_list = values.get("auto_approve_tools") or []
    auto_approve = set(auto_approve_list) if isinstance(auto_approve_list, list) else set()
    yolo_mode = bool(values.get("yolo_mode"))
    dangerous_setting = values.get("dangerous_always_approve")
    # Default-on: irreversible writes stay gated unless the user opts out.
    dangerous_always_approve = (
        bool(dangerous_setting) if isinstance(dangerous_setting, bool) else True
    )

    # The stored "system_prompt" setting is treated as an *extension*
    # appended to the hardcoded base + live integration capabilities,
    # so the LLM always knows it's PM Assistant and what it can do.
    # Wrap the assembly in a builder so AgentSession can re-run it every
    # turn — picks up live integration state and settings changes
    # without forcing a session drop. When nothing changed the prompt
    # bytes stay identical, so prefix caching still hits.
    language_raw = values.get("language")
    language = language_raw if isinstance(language_raw, str) else "en"

    async def _builder() -> str:
        latest = await read_settings_values()
        extension = latest.get("system_prompt") or None
        live_yolo = bool(latest.get("yolo_mode"))
        live_lang_raw = latest.get("language")
        live_lang = live_lang_raw if isinstance(live_lang_raw, str) else "en"
        return build_system_prompt(
            mcp, extension, yolo_mode=live_yolo, language=live_lang
        )

    initial_prompt = build_system_prompt(
        mcp,
        values.get("system_prompt") or None,
        yolo_mode=yolo_mode,
        language=language,
    )

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
        approval_timeout=APPROVAL_TIMEOUT_SECONDS,
        yolo_mode=yolo_mode,
        dangerous_always_approve=dangerous_always_approve,
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


def _clean_generated_title(raw: str) -> str:
    """Sanitise a model-produced title.

    Strips quotes/markdown bullets, takes first non-empty line, clamps length.
    Returns "" if nothing usable remains.
    """
    if not raw:
        return ""
    line = next((line for line in raw.splitlines() if line.strip()), "")
    line = line.strip()
    # Strip wrapping quotes the model often adds.
    for q in ('"', "'", "“", "”", "«", "»"):
        if line.startswith(q):
            line = line[1:]
        if line.endswith(q):
            line = line[:-1]
    # Strip leading list/heading markers.
    line = line.lstrip("-•*# ").rstrip(" .;:,")
    return line[:TITLE_MAX_LEN]


async def _is_first_user_message(chat_id: int) -> tuple[bool, str | None]:
    """Return (needs_title, current_title).

    "Needs title" when the conversation's title is the placeholder default
    AND no user message has been persisted yet for this chat.
    """
    async with async_session_factory() as session:
        convo = await session.get(Conversation, chat_id)
        if convo is None:
            return False, None
        if convo.title and convo.title.strip() not in ("", "New chat"):
            return False, convo.title
        count = (
            await session.execute(
                select(func.count())
                .select_from(Message)
                .where(
                    Message.conversation_id == chat_id,
                    Message.role == "user",
                )
            )
        ).scalar_one()
        return count == 0, convo.title


async def _generate_and_save_title(
    chat_id: int, first_message: str, llm: Any
) -> str | None:
    """Ask the title model for a short label, persist it, return it."""
    snippet = first_message.strip()
    if len(snippet) > 1200:
        snippet = snippet[:1200] + "…"
    system = (
        "You generate concise chat titles. "
        "Return only the title — 3 to 6 words, no quotes, no trailing "
        "punctuation, no markdown. Match the user's language (e.g. Persian "
        "stays Persian, English stays English)."
    )
    user = f"Summarise this first user message into a short title:\n\n{snippet}"
    try:
        completion = await llm.chat(
            model=TITLE_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=40,
            temperature=0.3,
            # Grok 4.1 Fast supports optional reasoning; disable it so the
            # full token budget goes to the title and we keep latency low.
            extra_body={"reasoning": {"enabled": False}},
        )
    except Exception as exc:  # noqa: BLE001 — title gen must never break chat
        log.warning("title generation failed for chat %s: %s", chat_id, exc)
        return None
    raw = ""
    try:
        raw = completion.choices[0].message.content or ""
    except (AttributeError, IndexError):
        return None
    title = _clean_generated_title(raw)
    if not title:
        return None
    try:
        async with async_session_factory() as session:
            convo = await session.get(Conversation, chat_id)
            if convo is None:
                return None
            # Don't overwrite a title the user manually set during the turn.
            if convo.title and convo.title.strip() not in ("", "New chat"):
                return None
            convo.title = title
            convo.updated_at = datetime.now(UTC)
            session.add(convo)
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("title persist failed for chat %s: %s", chat_id, exc)
        return None
    return title


@router.post("/{chat_id}/messages")
async def post_message(chat_id: int, body: MessageBody, request: Request) -> StreamingResponse:
    async with async_session_factory() as db_session:
        convo = await db_session.get(Conversation, chat_id)
        if convo is None:
            raise HTTPException(status_code=404, detail="chat not found")
        # Rule-activity chats are an audit timeline of automated turns.
        # Letting the user post into them would (a) stomp on a parked
        # AgentSession registered by the rule engine under _sessions[chat_id],
        # mixing user input into the rule's pending tool call, and (b)
        # blur the line between "things you asked for" and "things rules
        # did on your behalf". Reject at the API edge.
        if convo.kind in ("rule_activity", "system_rules_activity"):
            raise HTTPException(
                status_code=400, detail="this chat is read-only"
            )

    # Refuse a second concurrent turn for this chat. The lock is held for
    # the lifetime of the StreamingResponse and released in the generator's
    # `finally` (covers normal completion, errors, and client disconnect).
    # Non-blocking acquire: a contended lock means a turn is already in
    # flight, so we 409 rather than queue. Check-then-acquire is race-free
    # because there is no `await` between `locked()` and `acquire()`.
    turn_lock = _turn_lock_for(chat_id)
    if turn_lock.locked():
        raise HTTPException(status_code=409, detail="turn_in_progress")
    await turn_lock.acquire()

    try:
        text = body.text
        await _get_or_create_session(chat_id, request)

        needs_title, _ = await _is_first_user_message(chat_id)
        llm = getattr(request.app.state, "llm", None)
        title_task: asyncio.Task[str | None] | None = None
        if needs_title and llm is not None and text.strip():
            title_task = asyncio.create_task(
                _generate_and_save_title(chat_id, text, llm)
            )
    except BaseException:
        # Anything failing before we hand the generator back to Starlette
        # would otherwise leave the lock pinned forever — release it here.
        _release_turn_lock(chat_id)
        raise

    return StreamingResponse(
        _make_event_gen(chat_id, text, title_task)(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _make_event_gen(
    chat_id: int,
    text: str,
    title_task: asyncio.Task[str | None] | None,
):
    """Build the SSE generator for a turn.

    The per-chat turn lock is assumed already held by the caller; this
    generator owns releasing it in `finally` so the chat can't wedge in a
    permanent 409 on completion, error, or client disconnect.
    """

    async def event_gen():
        agent_session = _sessions.get(chat_id)
        try:
            if agent_session is None:
                yield _format_sse(
                    "error", {"event_id": -1, "error": "no active agent session"}
                )
                return
            try:
                async for evt in agent_session.run_turn(text):
                    etype = evt.get("type", "message")
                    payload = {k: v for k, v in evt.items() if k != "type"}
                    yield _format_sse(etype, payload)
            except asyncio.CancelledError:
                agent_session.cancel_pending()
                if title_task is not None and not title_task.done():
                    title_task.cancel()
                raise
            except Exception as exc:
                yield _format_sse(
                    "error", {"event_id": -1, "error": f"stream failed: {exc}"}
                )
            if title_task is not None:
                try:
                    new_title = await asyncio.wait_for(
                        title_task, timeout=TITLE_TIMEOUT_SECONDS
                    )
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    new_title = None
                except Exception as exc:  # noqa: BLE001
                    log.warning("title task crashed for chat %s: %s", chat_id, exc)
                    new_title = None
                if new_title:
                    yield _format_sse(
                        "title_updated", {"chat_id": chat_id, "title": new_title}
                    )
        finally:
            _release_turn_lock(chat_id)

    return event_gen


@router.post("/{chat_id}/approve", status_code=status.HTTP_204_NO_CONTENT)
async def approve_tool_call(chat_id: int, body: ApproveBody) -> None:
    session = _sessions.get(chat_id)
    if session is None:
        raise HTTPException(status_code=404, detail="no active agent session for chat")
    ok = session.approve(body.tool_call_id, body.approved)
    if not ok:
        raise HTTPException(status_code=404, detail="no pending approval for that tool_call_id")

    # When the approved/rejected call belongs to a rule-activity chat,
    # flip the most-recent `pending_approval` firing so digest counts
    # converge instead of accumulating ghost rows forever.
    async with async_session_factory() as db:
        convo = await db.get(Conversation, chat_id)
        if convo is not None and convo.kind in (
            "rule_activity",
            "system_rules_activity",
        ):
            stmt = (
                select(RuleFiring)
                .where(RuleFiring.conversation_id == chat_id)
                .where(RuleFiring.status == "pending_approval")
                .order_by(desc(RuleFiring.fired_at), desc(RuleFiring.id))
                .limit(1)
            )
            firing = (await db.execute(stmt)).scalar_one_or_none()
            if firing is not None:
                firing.status = "matched" if body.approved else "rejected"
                db.add(firing)
                await db.commit()
    return None


# Exposed for tests / lifespan teardown
def _reset_sessions() -> None:
    for sess in list(_sessions.values()):
        sess.cancel_pending()
    _sessions.clear()
