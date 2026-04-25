"""API endpoints for user-defined proactive rules.

Rules are natural-language automations compiled into structured
`CompiledSpec` JSON (see `services.rule_compiler`) and executed by a
background scheduler (see `services.rule_scheduler`). This router owns
the CRUD surface plus a manual `run-now` hook for testing.

Shape contract with the frontend (Wave 3 / Agent D):
- Request: CreateRuleRequest / UpdateRuleRequest
- Response: RuleResponse (embeds parsed `compiled_spec` + `recent_firings`)
- `run-now` returns a single `RuleFiringResponse`.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, desc, func, select

from db.models import Conversation, Message, Rule, RuleFiring
from db.session import async_session_factory
from services.rule_compiler import (
    CompiledSpec,
    RuleCompilationError,
    compile_rule,
)

router = APIRouter(prefix="/api/rules", tags=["rules"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


MIN_INTERVAL_SECONDS = 60


class CreateRuleRequest(BaseModel):
    description: str = Field(..., min_length=1)
    interval_seconds: int = Field(..., ge=MIN_INTERVAL_SECONDS)
    auto_approve: bool = False


class UpdateRuleRequest(BaseModel):
    # None means "do not change". compiled_spec arrives as a dict so the
    # frontend can present the compiled form as an editable form.
    compiled_spec: dict[str, Any] | None = None
    interval_seconds: int | None = Field(default=None, ge=MIN_INTERVAL_SECONDS)
    auto_approve: bool | None = None
    enabled: bool | None = None


class RuleFiringResponse(BaseModel):
    # id is nullable because `run-now` may return a transient, unsaved firing
    # (e.g. the no_match fast path in rule_engine.tick never touches the DB).
    id: int | None = None
    rule_id: int
    fired_at: datetime
    status: str
    match_summary: str | None = None
    message_id: int | None = None
    error: str | None = None


class RuleResponse(BaseModel):
    id: int
    description: str
    compiled_spec: dict[str, Any]
    interval_seconds: int
    auto_approve: bool
    enabled: bool
    state_cursor: str | None = None
    last_run_at: datetime | None = None
    last_error: str | None = None
    activity_conversation_id: int | None = None
    created_at: datetime
    updated_at: datetime
    recent_firings: list[RuleFiringResponse] = Field(default_factory=list)


class FiringTimelineRow(RuleFiringResponse):
    """Row shape for `GET /api/rules/{id}/firings`. Adds denormalized fields
    so the client can render the timeline without joining through Rule.
    """

    conversation_id: int | None = None
    trigger_summary: str | None = None
    duration_ms: int | None = None


class RuleDigest(BaseModel):
    window: str
    window_from: datetime = Field(..., alias="from")
    window_to: datetime = Field(..., alias="to")
    matched: int = 0
    no_match: int = 0
    error: int = 0
    pending_approval: int = 0
    expired: int = 0
    rejected: int = 0
    skipped: int = 0
    first_match_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error_text: str | None = None
    # 24 hourly buckets of (matched + error) counts.
    sparkline: list[int] = Field(default_factory=lambda: [0] * 24)

    model_config = {"populate_by_name": True}


class RuleHealthResponse(BaseModel):
    rule: RuleResponse
    digest_24h: RuleDigest
    consecutive_failures: int


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _firing_out(f: RuleFiring) -> RuleFiringResponse:
    return RuleFiringResponse(
        id=f.id,  # type: ignore[arg-type]
        rule_id=f.rule_id,
        fired_at=f.fired_at,
        status=f.status,
        match_summary=f.match_summary,
        message_id=f.message_id,
        error=f.error,
    )


def _timeline_row_out(f: RuleFiring) -> FiringTimelineRow:
    return FiringTimelineRow(
        id=f.id,  # type: ignore[arg-type]
        rule_id=f.rule_id,
        fired_at=f.fired_at,
        status=f.status,
        match_summary=f.match_summary,
        message_id=f.message_id,
        error=f.error,
        conversation_id=f.conversation_id,
        trigger_summary=f.trigger_summary,
        duration_ms=f.duration_ms,
    )


def _parse_compiled_spec(raw: str) -> dict[str, Any]:
    """Parse the stored JSON, returning {} on corruption.

    We never want to 500 a GET /api/rules because one historical row has
    stale JSON — the UI can render the original `description` as a fallback.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _rule_out(r: Rule, firings: list[RuleFiring] | None = None) -> RuleResponse:
    return RuleResponse(
        id=r.id,  # type: ignore[arg-type]
        description=r.description,
        compiled_spec=_parse_compiled_spec(r.compiled_spec),
        interval_seconds=r.interval_seconds,
        auto_approve=r.auto_approve,
        enabled=r.enabled,
        state_cursor=r.state_cursor,
        last_run_at=r.last_run_at,
        last_error=r.last_error,
        activity_conversation_id=r.activity_conversation_id,
        created_at=r.created_at,
        updated_at=r.updated_at,
        recent_firings=[_firing_out(f) for f in (firings or [])],
    )


async def _allocate_activity_chat(session: Any, rule: Rule) -> None:
    """Allocate + back-link a per-rule activity conversation if missing."""
    if rule.activity_conversation_id is not None:
        return
    title_seed = (rule.description or f"rule {rule.id}").strip() or f"rule {rule.id}"
    convo = Conversation(
        title=f"Rule: {title_seed[:60]}",
        kind="rule_activity",
    )
    session.add(convo)
    await session.commit()
    await session.refresh(convo)
    rule.activity_conversation_id = convo.id
    session.add(rule)
    await session.commit()
    await session.refresh(rule)


async def _recent_firings_for(session: Any, rule_id: int, limit: int = 5) -> list[RuleFiring]:
    stmt = (
        select(RuleFiring)
        .where(RuleFiring.rule_id == rule_id)
        .order_by(desc(RuleFiring.fired_at), desc(RuleFiring.id))
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


# ---------------------------------------------------------------------------
# App-state accessors
# ---------------------------------------------------------------------------


def _llm_and_mcp(request: Request) -> tuple[Any, Any]:
    llm = getattr(request.app.state, "llm", None)
    mcp = getattr(request.app.state, "mcp", None)
    if llm is None or mcp is None:
        raise HTTPException(status_code=503, detail="server not ready")
    return llm, mcp


def _default_model(request: Request) -> str:
    settings = getattr(request.app.state, "settings", None)
    model = getattr(settings, "openrouter_default_model", None)
    return model or "google/gemini-3-flash-preview"


async def _scheduler_reload(request: Request) -> None:
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None:
        return
    reload = getattr(scheduler, "reload", None)
    if reload is None:
        return
    try:
        await reload()
    except Exception:
        # Scheduler must never take down the API; the firings log surfaces errors.
        pass


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", response_model=RuleResponse, status_code=status.HTTP_201_CREATED)
async def create_rule(body: CreateRuleRequest, request: Request) -> RuleResponse:
    llm, mcp = _llm_and_mcp(request)
    model = _default_model(request)

    try:
        spec: CompiledSpec = await compile_rule(
            body.description, mcp, llm, model=model
        )
    except RuleCompilationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    now = datetime.now(UTC)
    rule = Rule(
        description=body.description,
        compiled_spec=spec.model_dump_json(),
        interval_seconds=body.interval_seconds,
        auto_approve=body.auto_approve,
        enabled=True,
        created_at=now,
        updated_at=now,
    )
    async with async_session_factory() as session:
        session.add(rule)
        await session.commit()
        await session.refresh(rule)

        await _allocate_activity_chat(session, rule)

    await _scheduler_reload(request)
    return _rule_out(rule, firings=[])


@router.get("", response_model=list[RuleResponse])
async def list_rules() -> list[RuleResponse]:
    async with async_session_factory() as session:
        stmt = select(Rule).order_by(desc(Rule.created_at), desc(Rule.id))
        rules = list((await session.execute(stmt)).scalars().all())
        out: list[RuleResponse] = []
        for r in rules:
            firings = await _recent_firings_for(session, r.id) if r.id is not None else []
            out.append(_rule_out(r, firings=firings))
    return out


@router.get("/health", response_model=list[RuleHealthResponse])
async def list_rule_health() -> list[RuleHealthResponse]:
    """Dashboard round-trip: one call returns every rule + its 24h digest
    + its current consecutive-failure counter.
    """
    from services.rule_engine import get_consecutive_error_count

    async with async_session_factory() as session:
        stmt = select(Rule).order_by(desc(Rule.created_at), desc(Rule.id))
        rules = list((await session.execute(stmt)).scalars().all())

        out: list[RuleHealthResponse] = []
        for r in rules:
            if r.id is None:
                continue
            firings = await _recent_firings_for(session, r.id)
            digest = await _compute_digest(session, r.id)
            out.append(
                RuleHealthResponse(
                    rule=_rule_out(r, firings=firings),
                    digest_24h=digest,
                    consecutive_failures=get_consecutive_error_count(r.id),
                )
            )
    return out


@router.patch("/{rule_id}", response_model=RuleResponse)
async def update_rule(
    rule_id: int, body: UpdateRuleRequest, request: Request
) -> RuleResponse:
    async with async_session_factory() as session:
        rule = await session.get(Rule, rule_id)
        if rule is None:
            raise HTTPException(status_code=404, detail="rule not found")

        if body.compiled_spec is not None:
            try:
                validated = CompiledSpec.model_validate(body.compiled_spec)
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"invalid compiled_spec: {exc}",
                ) from exc
            rule.compiled_spec = validated.model_dump_json()

        if body.interval_seconds is not None:
            rule.interval_seconds = body.interval_seconds

        if body.auto_approve is not None:
            rule.auto_approve = body.auto_approve

        if body.enabled is not None:
            rule.enabled = body.enabled

        rule.updated_at = datetime.now(UTC)
        session.add(rule)
        await session.commit()
        await session.refresh(rule)

        firings = await _recent_firings_for(session, rule_id)

    await _scheduler_reload(request)
    return _rule_out(rule, firings=firings)


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(rule_id: int, request: Request) -> None:
    async with async_session_factory() as session:
        rule = await session.get(Rule, rule_id)
        if rule is None:
            raise HTTPException(status_code=404, detail="rule not found")

        activity_id = rule.activity_conversation_id

        await session.execute(delete(RuleFiring).where(RuleFiring.rule_id == rule_id))
        await session.delete(rule)
        await session.commit()

        # Cascade the bound activity chat + its messages.
        if activity_id is not None:
            await session.execute(
                delete(Message).where(Message.conversation_id == activity_id)
            )
            convo = await session.get(Conversation, activity_id)
            if convo is not None:
                await session.delete(convo)
            await session.commit()

    # Drop any in-memory AgentSession parked against the bound chat. Import
    # is intentionally late: api.stream imports us indirectly via lifespan
    # registration and we don't want a hard module cycle.
    if activity_id is not None:
        try:
            from api.stream import _sessions

            _sessions.pop(activity_id, None)
        except Exception:
            pass

    await _scheduler_reload(request)
    return None


@router.post("/{rule_id}/run-now", response_model=RuleFiringResponse)
async def run_rule_now(rule_id: int, request: Request) -> RuleFiringResponse:
    llm, mcp = _llm_and_mcp(request)
    settings = getattr(request.app.state, "settings", None)
    model = _default_model(request)

    async with async_session_factory() as session:
        rule = await session.get(Rule, rule_id)
        if rule is None:
            raise HTTPException(status_code=404, detail="rule not found")

    # Delay-import the engine so tests can monkeypatch the symbol without
    # requiring Agent B's module to exist at collection time.
    try:
        from services import rule_engine  # type: ignore
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"rule engine unavailable: {exc}",
        ) from exc

    try:
        firing: RuleFiring = await rule_engine.tick(
            rule_id,
            mcp=mcp,
            llm=llm,
            settings=settings,
            db_factory=async_session_factory,
            default_model=model,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"rule tick failed: {exc}",
        ) from exc

    return _firing_out(firing)


# ---------------------------------------------------------------------------
# Timeline / digest / health
# ---------------------------------------------------------------------------


_DEFAULT_FIRINGS_LIMIT = 50
_MAX_FIRINGS_LIMIT = 200


def _parse_status_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]


@router.get("/{rule_id}/firings", response_model=list[FiringTimelineRow])
async def get_rule_firings(
    rule_id: int,
    limit: int = Query(_DEFAULT_FIRINGS_LIMIT, ge=1, le=_MAX_FIRINGS_LIMIT),
    status: str | None = None,
    since: datetime | None = None,
) -> list[FiringTimelineRow]:
    """Per-rule timeline. Ordered newest-first.

    `status` is a CSV (e.g. ``matched,error``); absent → no filter.
    `since` filters to firings with ``fired_at >= since``.
    """
    async with async_session_factory() as session:
        rule = await session.get(Rule, rule_id)
        if rule is None:
            raise HTTPException(status_code=404, detail="rule not found")

        stmt = select(RuleFiring).where(RuleFiring.rule_id == rule_id)
        wanted_statuses = _parse_status_csv(status)
        if wanted_statuses:
            stmt = stmt.where(RuleFiring.status.in_(wanted_statuses))
        if since is not None:
            stmt = stmt.where(RuleFiring.fired_at >= since)
        stmt = (
            stmt.order_by(desc(RuleFiring.fired_at), desc(RuleFiring.id))
            .limit(limit)
        )
        rows = list((await session.execute(stmt)).scalars().all())
    return [_timeline_row_out(f) for f in rows]


async def _compute_digest(
    session: Any, rule_id: int, window: str = "24h"
) -> RuleDigest:
    """Server-side 24h aggregation: counts + last error + hourly sparkline.

    One SQL group-by for status counts, one for hourly buckets. Chatty
    (1440/day no_match) rules can't bog down the wire.
    """
    now = datetime.now(UTC)
    if window != "24h":
        # Only 24h supported for now; fall through to 24h regardless.
        window = "24h"
    window_from = now - timedelta(hours=24)

    # ---- counts by status ----
    counts_stmt = (
        select(RuleFiring.status, func.count(RuleFiring.id))
        .where(RuleFiring.rule_id == rule_id)
        .where(RuleFiring.fired_at >= window_from)
        .group_by(RuleFiring.status)
    )
    counts_rows = (await session.execute(counts_stmt)).all()
    counts: dict[str, int] = {s: int(c) for s, c in counts_rows}

    # ---- first match ----
    first_match_stmt = (
        select(func.min(RuleFiring.fired_at))
        .where(RuleFiring.rule_id == rule_id)
        .where(RuleFiring.fired_at >= window_from)
        .where(RuleFiring.status == "matched")
    )
    first_match_at = (await session.execute(first_match_stmt)).scalar()

    # ---- last error ----
    last_error_stmt = (
        select(RuleFiring.fired_at, RuleFiring.error)
        .where(RuleFiring.rule_id == rule_id)
        .where(RuleFiring.fired_at >= window_from)
        .where(RuleFiring.status == "error")
        .order_by(desc(RuleFiring.fired_at), desc(RuleFiring.id))
        .limit(1)
    )
    last_error_row = (await session.execute(last_error_stmt)).first()
    last_error_at = last_error_row[0] if last_error_row else None
    last_error_text = last_error_row[1] if last_error_row else None

    # ---- hourly buckets (matched + error) ----
    sparkline = await _compute_hourly_sparkline(
        session, rule_id=rule_id, window_from=window_from, window_to=now
    )

    return RuleDigest.model_validate(
        {
            "window": window,
            "from": window_from,
            "to": now,
            "matched": counts.get("matched", 0),
            "no_match": counts.get("no_match", 0),
            "error": counts.get("error", 0),
            "pending_approval": counts.get("pending_approval", 0),
            "expired": counts.get("expired", 0),
            "rejected": counts.get("rejected", 0),
            "skipped": counts.get("skipped", 0),
            "first_match_at": first_match_at,
            "last_error_at": last_error_at,
            "last_error_text": last_error_text,
            "sparkline": sparkline,
        }
    )


async def _compute_hourly_sparkline(
    session: Any, *, rule_id: int, window_from: datetime, window_to: datetime
) -> list[int]:
    """Return 24 ints — count of (matched + error) firings in each hour bucket.

    One query over the window, bucketed in Python (SQLite's date funcs are
    less ergonomic than a tight loop over ~a few thousand rows, and the
    window is bounded at 24h so volume is manageable).
    """
    stmt = (
        select(RuleFiring.fired_at, RuleFiring.status)
        .where(RuleFiring.rule_id == rule_id)
        .where(RuleFiring.fired_at >= window_from)
        .where(RuleFiring.fired_at <= window_to)
        .where(RuleFiring.status.in_(["matched", "error"]))
    )
    rows = (await session.execute(stmt)).all()
    buckets = [0] * 24
    total_seconds = (window_to - window_from).total_seconds()
    if total_seconds <= 0:
        return buckets
    for fired_at, _status in rows:
        if fired_at is None:
            continue
        # Normalize to aware UTC so the subtraction doesn't raise.
        fa = fired_at if fired_at.tzinfo is not None else fired_at.replace(tzinfo=UTC)
        delta = (fa - window_from).total_seconds()
        if delta < 0 or delta > total_seconds:
            continue
        idx = int(delta / 3600.0)
        if idx >= 24:
            idx = 23
        if idx < 0:
            idx = 0
        buckets[idx] += 1
    return buckets


@router.get("/{rule_id}/digest", response_model=RuleDigest)
async def get_rule_digest(
    rule_id: int, window: str = Query("24h")
) -> RuleDigest:
    """Server-side aggregation for a rule: 24h counts + sparkline."""
    async with async_session_factory() as session:
        rule = await session.get(Rule, rule_id)
        if rule is None:
            raise HTTPException(status_code=404, detail="rule not found")
        return await _compute_digest(session, rule_id, window=window)
