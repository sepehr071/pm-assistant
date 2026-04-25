"""Rule engine — periodic tick that polls a single MCP tool, applies a closed-set
filter against the result, and (on match) runs a headless `AgentSession` against
the "Rules activity" conversation.

The hot path is deliberately LLM-free: we only call the read-side MCP tool and a
pure-python matcher. An LLM turn only fires when something matched, via
`AgentSession.run_turn` — which reuses the exact same approval gate semantics as
the interactive UI, so `POST /api/chats/{id}/approve` keeps working unchanged
for rule-spawned turns.

Error handling is load-bearing: *no* exception may propagate out of `tick` —
the scheduler job wraps us but we still defuse everything here so a single
broken tool response can't take down the loop. Five consecutive errors on the
same rule auto-disable it (surfaced via `rule.last_error` in the UI).
"""
from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from agent.loop import AgentSession
from db.models import Conversation, Rule, RuleFiring
from services.rule_compiler import CompiledFilter, CompiledSpec

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level consecutive-error counter keyed by rule_id.
# Kept simple on purpose — one process, one scheduler, no cross-process state.
# ---------------------------------------------------------------------------

_consecutive_errors: dict[int, int] = {}
_AUTO_DISABLE_THRESHOLD = 5


def reset_error_counters() -> None:
    """Test helper — wipes the in-memory consecutive-error counter."""
    _consecutive_errors.clear()


def get_consecutive_error_count(rule_id: int) -> int:
    """Read-only accessor for the private consecutive-error counter.

    Used by `GET /api/rules/health` so the dashboard can warn before the
    auto-disable threshold fires.
    """
    return _consecutive_errors.get(rule_id, 0)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def tick(
    rule_id: int,
    *,
    mcp: Any,
    llm: Any,
    settings: Any,
    db_factory: async_sessionmaker[AsyncSession],
    default_model: str,
) -> RuleFiring:
    """Run one tick of `rule_id`. Returns the `RuleFiring` row (possibly
    transient — the `no_match` fast path does not persist a firing, and returns
    an unsaved instance the caller can ignore).
    """
    tick_start = time.perf_counter()
    source_tool_label = ""

    # -- load --------------------------------------------------------------
    async with db_factory() as session:
        rule = await session.get(Rule, rule_id)
        if rule is None or not rule.enabled:
            return RuleFiring(rule_id=rule_id, status="no_match")

        try:
            spec = _parse_spec(rule.compiled_spec)
        except ValueError as exc:
            return await _record_error(
                session, rule, f"invalid compiled_spec: {exc}", tick_start
            )

        rule.last_run_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(rule)
        source_tool_label = spec.source_tool

    # -- poll source tool --------------------------------------------------
    try:
        raw = await mcp.call_tool(spec.source_tool, dict(spec.source_args or {}))
    except Exception as exc:  # pragma: no cover — defensive; most errors return is_error
        logger.exception("MCP tool call raised for rule %s", rule_id)
        async with db_factory() as session:
            rule = await session.get(Rule, rule_id)
            if rule is None:
                return RuleFiring(rule_id=rule_id, status="error", error=str(exc))
            return await _record_error(
                session, rule, f"tool call raised: {exc}", tick_start
            )

    if _is_tool_error(raw):
        err_text = _extract_error_text(raw)
        async with db_factory() as session:
            rule = await session.get(Rule, rule_id)
            if rule is None:
                return RuleFiring(rule_id=rule_id, status="error", error=err_text)
            return await _record_error(session, rule, err_text, tick_start)

    # -- match -------------------------------------------------------------
    try:
        match = _apply_filter(spec.filter, raw, rule.state_cursor)
    except Exception as exc:
        logger.exception("filter matcher crashed for rule %s", rule_id)
        async with db_factory() as session:
            rule = await session.get(Rule, rule_id)
            if rule is None:
                return RuleFiring(rule_id=rule_id, status="error", error=str(exc))
            return await _record_error(
                session, rule, f"matcher crashed: {exc}", tick_start
            )

    # Success path resets the consecutive-error counter regardless of match.
    _consecutive_errors.pop(rule_id, None)

    if match is None:
        async with db_factory() as session:
            rule = await session.get(Rule, rule_id)
            if rule is not None:
                rule.last_error = None
                await session.commit()
        return RuleFiring(rule_id=rule_id, status="no_match")

    # -- resolve (or lazy-create) the per-rule activity chat --------------
    async with db_factory() as session:
        rule = await session.get(Rule, rule_id)
        if rule is None:
            # Rule vanished mid-tick; nothing to do.
            return RuleFiring(rule_id=rule_id, status="no_match")
        activity_id = await _get_or_create_rule_activity_chat(session, rule)

    # -- serialize against concurrent user use of the activity chat --------
    # Importing locally so the scheduler doesn't hard-depend on api.stream at
    # module-load time (keeps this unit-testable without FastAPI wired up).
    from api.stream import _sessions

    if activity_id in _sessions:
        logger.info(
            "rule %s: activity chat %s already has a live session — skipping tick",
            rule_id,
            activity_id,
        )
        async with db_factory() as session:
            firing = RuleFiring(
                rule_id=rule_id,
                status="skipped",
                match_summary=match.summary,
                conversation_id=activity_id,
                duration_ms=_elapsed_ms(tick_start),
                trigger_summary=_truncate(
                    f"skipped (activity chat busy): {match.summary}"
                ),
            )
            session.add(firing)
            await session.commit()
            await session.refresh(firing)
            return firing

    # -- run a headless agent turn ----------------------------------------
    synthetic_prompt = _build_synthetic_prompt(
        rule_description=await _load_rule_description(db_factory, rule_id),
        match_summary=match.summary,
        action_prompt=spec.action_prompt,
    )

    agent_session = AgentSession(
        chat_id=activity_id,
        llm=llm,
        mcp=mcp,
        settings=settings,
        auto_approve=set(),
        db_factory=db_factory,
        default_model=default_model,
        yolo_mode=await _load_auto_approve(db_factory, rule_id),
    )
    _sessions[activity_id] = agent_session

    status = "matched"
    message_id: int | None = None
    error: str | None = None
    try:
        async for event in agent_session.run_turn(synthetic_prompt):
            etype = event.get("type")
            if etype == "tool_call_request":
                status = "pending_approval"
                # Leave the session registered so /approve can find it, then
                # persist + return. The turn continues in the background if
                # the user approves; a subsequent tick will find the session
                # already present and skip until the user resolves it.
                async with db_factory() as db:
                    firing = RuleFiring(
                        rule_id=rule_id,
                        status="pending_approval",
                        match_summary=match.summary,
                        conversation_id=activity_id,
                        duration_ms=_elapsed_ms(tick_start),
                        trigger_summary=_truncate(match.summary),
                    )
                    db.add(firing)
                    await db.commit()
                    await db.refresh(firing)
                    return firing
            elif etype == "done":
                message_id = event.get("message_id")
                status = "matched"
            elif etype == "error":
                status = "error"
                error = event.get("error")
                break
    finally:
        # If we didn't leave the session parked for an approval, drop it.
        if status != "pending_approval":
            _sessions.pop(activity_id, None)
            agent_session.cancel_pending()

    # -- persist firing + cursor ------------------------------------------
    async with db_factory() as session:
        rule = await session.get(Rule, rule_id)
        if rule is not None and status == "matched" and match.new_cursor is not None:
            rule.state_cursor = match.new_cursor
            rule.last_error = None
        if rule is not None and status == "error":
            rule.last_error = error
        if status == "matched":
            trigger_summary = _truncate(match.summary)
        elif status == "error":
            trigger_summary = _truncate((error or "").splitlines()[0] if error else "error")
        else:
            trigger_summary = _truncate(match.summary)
        firing = RuleFiring(
            rule_id=rule_id,
            status=status,
            match_summary=match.summary,
            message_id=message_id,
            error=error,
            conversation_id=activity_id,
            duration_ms=_elapsed_ms(tick_start),
            trigger_summary=trigger_summary,
        )
        session.add(firing)
        await session.commit()
        await session.refresh(firing)
        return firing


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MatchResult:
    __slots__ = ("summary", "new_cursor")

    def __init__(self, summary: str, new_cursor: str | None) -> None:
        self.summary = summary
        self.new_cursor = new_cursor


def _parse_spec(raw: str) -> CompiledSpec:
    data = json.loads(raw)
    return CompiledSpec.model_validate(data)


def _is_tool_error(raw: Any) -> bool:
    if isinstance(raw, dict):
        return bool(raw.get("is_error") or raw.get("isError"))
    return bool(getattr(raw, "is_error", False) or getattr(raw, "isError", False))


def _extract_error_text(raw: Any) -> str:
    if isinstance(raw, dict):
        content = raw.get("content")
    else:
        content = getattr(raw, "content", None)
    if isinstance(content, str):
        return content or "tool error"
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
            else:
                text = getattr(item, "text", None)
            if isinstance(text, str) and text:
                parts.append(text)
        if parts:
            return "\n".join(parts)
    return str(content) if content is not None else "tool error"


def _tool_result_payload(raw: Any) -> Any:
    """Extract the 'structured content' or parsed JSON body from an MCP result.

    We tolerate three shapes:
      1. {"structured": <dict>} — preferred, Smithery's structuredContent.
      2. {"content": "<json string>"} — some servers serialize to text.
      3. {"content": [...]} — list of records already parsed.
    """
    if isinstance(raw, dict):
        structured = raw.get("structured") or raw.get("structuredContent")
        if structured is not None:
            return structured
        content = raw.get("content")
    else:
        structured = getattr(raw, "structured", None) or getattr(
            raw, "structuredContent", None
        )
        if structured is not None:
            return structured
        content = getattr(raw, "content", None)

    if isinstance(content, str):
        text = content.strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}
    return content


def _apply_filter(
    filter_: CompiledFilter, raw: Any, cursor: str | None
) -> _MatchResult | None:
    """Dispatch on `filter.kind`. Defensive: any unexpected shape returns None
    instead of crashing — we prefer a missed match over a broken scheduler.
    """
    payload = _tool_result_payload(raw)

    if filter_.kind == "message_from_user_contains":
        return _match_slack_message(filter_, payload, cursor)
    if filter_.kind == "new_issue_mentions":
        return _match_new_issue(filter_, payload, cursor)
    if filter_.kind == "new_pr_review_request":
        return _match_new_pr_review(filter_, payload, cursor)
    if filter_.kind == "schedule_only":
        return _MatchResult(summary="schedule tick", new_cursor=cursor)

    logger.warning("unknown filter kind: %r", filter_.kind)
    return None


# ---- Slack message matcher ------------------------------------------------


def _extract_messages(payload: Any) -> list[dict[str, Any]]:
    """Pull a list of message records from whatever shape the tool returned."""
    if payload is None:
        return []
    if isinstance(payload, list):
        return [m for m in payload if isinstance(m, dict)]
    if isinstance(payload, dict):
        for key in ("messages", "items", "results", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [m for m in value if isinstance(m, dict)]
    return []


def _match_slack_message(
    f: CompiledFilter, payload: Any, cursor: str | None
) -> _MatchResult | None:
    user = (f.user or "").strip()
    contains = [c.lower() for c in (f.contains or []) if c]
    if not user or not contains:
        return None

    messages = _extract_messages(payload)
    newest_cursor = cursor or ""

    for msg in messages:
        ts = str(msg.get("ts") or msg.get("timestamp") or "")
        if ts and cursor and ts <= cursor:
            continue
        msg_user = str(
            msg.get("user")
            or msg.get("user_name")
            or (msg.get("user_profile") or {}).get("display_name")
            or msg.get("username")
            or ""
        )
        text = str(msg.get("text") or msg.get("message") or "").lower()
        if not text or not msg_user:
            continue
        if msg_user.lower() != user.lower():
            continue
        if not any(token in text for token in contains):
            continue
        if ts and ts > newest_cursor:
            newest_cursor = ts
        summary = (
            f"Slack message from {msg_user}: "
            f"{(msg.get('text') or '')[:140]}"
        )
        return _MatchResult(
            summary=summary, new_cursor=newest_cursor or None
        )

    return None


# ---- GitHub issue mention matcher ---------------------------------------


def _extract_records(payload: Any) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("issues", "items", "results", "data", "pull_requests", "prs"):
            value = payload.get(key)
            if isinstance(value, list):
                return [r for r in value if isinstance(r, dict)]
    return []


def _match_new_issue(
    f: CompiledFilter, payload: Any, cursor: str | None
) -> _MatchResult | None:
    mention = (f.mention or "").strip()
    if not mention:
        return None

    issues = _extract_records(payload)
    needle = mention.lower()
    newest = cursor or ""

    for issue in issues:
        updated = str(issue.get("updated_at") or issue.get("updatedAt") or "")
        if updated and cursor and updated <= cursor:
            continue
        title = str(issue.get("title") or "").lower()
        body = str(issue.get("body") or issue.get("description") or "").lower()
        if needle not in title and needle not in body:
            continue
        if updated and updated > newest:
            newest = updated
        summary = f"Issue mentions {mention}: {(issue.get('title') or '')[:140]}"
        return _MatchResult(summary=summary, new_cursor=newest or None)

    return None


def _match_new_pr_review(
    f: CompiledFilter, payload: Any, cursor: str | None
) -> _MatchResult | None:
    mention = (f.mention or "").strip()
    if not mention:
        return None

    prs = _extract_records(payload)
    needle = mention.lower()
    newest = cursor or ""

    for pr in prs:
        updated = str(pr.get("updated_at") or pr.get("updatedAt") or "")
        if updated and cursor and updated <= cursor:
            continue
        reviewers_raw = (
            pr.get("requested_reviewers")
            or pr.get("requestedReviewers")
            or []
        )
        if not isinstance(reviewers_raw, list):
            continue
        reviewer_names: list[str] = []
        for rev in reviewers_raw:
            if isinstance(rev, dict):
                reviewer_names.append(
                    str(rev.get("login") or rev.get("name") or "")
                )
            elif isinstance(rev, str):
                reviewer_names.append(rev)
        if not any(name.lower() == needle for name in reviewer_names):
            continue
        if updated and updated > newest:
            newest = updated
        summary = (
            f"PR review requested from {mention}: "
            f"{(pr.get('title') or '')[:140]}"
        )
        return _MatchResult(summary=summary, new_cursor=newest or None)

    return None


# ---- activity chat + rule metadata helpers ------------------------------


async def _get_or_create_rule_activity_chat(
    session: AsyncSession, rule: Rule
) -> int:
    """Return the id of `rule`'s activity conversation, creating it lazily.

    Each rule owns its own `Conversation(kind="rule_activity")` — this keeps
    `_sessions` (keyed by conversation id) from colliding across rules, so a
    parked approval on rule A no longer blocks rule B's tick.
    """
    if rule.activity_conversation_id is not None:
        existing = await session.get(Conversation, rule.activity_conversation_id)
        if existing is not None:
            return existing.id  # type: ignore[return-value]
        # Dangling FK — fall through and create a fresh chat.

    title_seed = (rule.description or f"rule {rule.id}").strip() or f"rule {rule.id}"
    convo = Conversation(title=f"Rule: {title_seed[:60]}", kind="rule_activity")
    session.add(convo)
    await session.commit()
    await session.refresh(convo)
    rule.activity_conversation_id = convo.id
    session.add(rule)
    await session.commit()
    return convo.id  # type: ignore[return-value]


def _elapsed_ms(start_perf_counter: float) -> int:
    return max(0, int((time.perf_counter() - start_perf_counter) * 1000))


def _truncate(text: str | None, limit: int = 140) -> str | None:
    if text is None:
        return None
    t = text.strip()
    if not t:
        return None
    return t if len(t) <= limit else t[: limit - 1] + "…"


async def _load_rule_description(
    db_factory: async_sessionmaker[AsyncSession], rule_id: int
) -> str:
    async with db_factory() as session:
        rule = await session.get(Rule, rule_id)
        return rule.description if rule is not None else f"rule {rule_id}"


async def _load_auto_approve(
    db_factory: async_sessionmaker[AsyncSession], rule_id: int
) -> bool:
    async with db_factory() as session:
        rule = await session.get(Rule, rule_id)
        return bool(rule.auto_approve) if rule is not None else False


def _build_synthetic_prompt(
    *, rule_description: str, match_summary: str, action_prompt: str
) -> str:
    return (
        f"[rule:{rule_description}]\n\n"
        f"Matched:\n{match_summary}\n\n"
        f"Action requested:\n{action_prompt}"
    )


# ---- error / counter helpers --------------------------------------------


async def _record_error(
    session: AsyncSession,
    rule: Rule,
    error_text: str,
    tick_start: float,
) -> RuleFiring:
    _consecutive_errors[rule.id or 0] = _consecutive_errors.get(rule.id or 0, 0) + 1
    rule.last_error = error_text
    if _consecutive_errors[rule.id or 0] >= _AUTO_DISABLE_THRESHOLD:
        rule.enabled = False

    # Best-effort: attach the rule's activity chat so timeline queries can
    # group error rows next to matched rows. Errors before the first match
    # still allocate the chat (cheap, happens once per rule).
    activity_id = rule.activity_conversation_id
    if activity_id is None:
        activity_id = await _get_or_create_rule_activity_chat(session, rule)

    first_line = (error_text or "").splitlines()[0] if error_text else "error"
    firing = RuleFiring(
        rule_id=rule.id or 0,
        status="error",
        error=error_text,
        conversation_id=activity_id,
        duration_ms=_elapsed_ms(tick_start),
        trigger_summary=_truncate(first_line),
    )
    session.add(firing)
    await session.commit()
    await session.refresh(firing)
    return firing
