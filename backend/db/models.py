from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Conversation(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    title: str
    # "user" | "system_rules_activity" (legacy singleton) | "rule_activity" (per-rule) | "telegram"
    kind: str = Field(default="user")
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    # When set, _load_history() ignores Message rows with created_at <= cutoff.
    # Non-destructive reset: lets Telegram /reset or a future "Clear chat"
    # button break a poisoned history without deleting audit data.
    history_cutoff_at: datetime | None = Field(default=None)


class Message(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    conversation_id: int = Field(foreign_key="conversation.id", index=True)
    role: str
    content: str | None = None
    tool_calls: str | None = None
    tool_call_id: str | None = None
    name: str | None = None
    # Indexed: history loads order by created_at; helps as the table grows.
    created_at: datetime = Field(default_factory=_utcnow, index=True)


class UserSetting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: str


class Rule(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    description: str  # original NL input
    compiled_spec: str  # JSON: {source_tool, source_args, filter, action_prompt}
    interval_seconds: int  # minimum 60, enforced at API layer
    auto_approve: bool = False
    enabled: bool = True
    state_cursor: str | None = None  # last-seen marker (e.g. Slack ts, GH updated_at)
    last_run_at: datetime | None = None
    last_error: str | None = None
    # Per-rule activity chat (lazy-allocated). Deleting a rule cascades this chat.
    activity_conversation_id: int | None = Field(
        default=None, foreign_key="conversation.id"
    )
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class RuleFiring(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    rule_id: int = Field(foreign_key="rule.id", index=True)
    # Timeline queries order by fired_at desc; index speeds them as the
    # table grows (one row per tick → ~7k rows/day for a 1-min rule).
    fired_at: datetime = Field(default_factory=_utcnow, index=True)
    status: str  # "matched" | "no_match" | "error" | "pending_approval" | "expired" | "rejected" | "skipped"
    match_summary: str | None = None  # short text of what matched
    message_id: int | None = None  # link to Message created in activity chat
    error: str | None = None
    # Denormalized FK so timeline queries don't join through Rule.
    conversation_id: int | None = Field(
        default=None, foreign_key="conversation.id", index=True
    )
    # Wall-clock duration of the tick that produced this firing (ms).
    duration_ms: int | None = None
    # Short (<=140 chars) label suitable for timeline rows. Distinct from
    # match_summary for no-match / error rows.
    trigger_summary: str | None = None
