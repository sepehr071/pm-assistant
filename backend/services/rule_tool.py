"""Builtin `rules__create_rule` tool for chat-driven rule creation.

Exposes a single locally-dispatched tool on `MCPManager` so the agent can
persist a proactive Rule from a chat conversation. The LLM is instructed
(via the system prompt) to first ask the user for `interval_seconds` and
`auto_approve`, then call this tool; the existing approval gate in the
agent loop fires normally so the user explicitly confirms the rule.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from db.models import Conversation, Rule
from services.rule_compiler import RuleCompilationError, compile_rule

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from mcp_manager import MCPManager


class _SchedulerLike(Protocol):
    async def reload(self) -> None: ...


RULE_CREATE_TOOL_SPEC: dict[str, Any] = {
    "type": "object",
    "properties": {
        "description": {
            "type": "string",
            "description": (
                "Natural-language description of the rule. State the "
                "trigger condition and the action to take, e.g. "
                "'whenever Sepehr DMs me on Slack about the roadmap, "
                "reply that I will get to it today'."
            ),
        },
        "interval_seconds": {
            "type": "integer",
            "minimum": 60,
            "description": (
                "How often (in seconds) the background scheduler should "
                "poll for the trigger. Minimum 60. Typical values: 60, "
                "300, 900, 3600."
            ),
        },
        "auto_approve": {
            "type": "boolean",
            "default": False,
            "description": (
                "If true, any write tool calls this rule issues will "
                "execute without pausing for user approval. Default false."
            ),
        },
    },
    "required": ["description", "interval_seconds"],
    "additionalProperties": False,
}


def _summarise(spec_dict: dict[str, Any], rule_id: int, interval: int, auto: bool) -> str:
    source = spec_dict.get("source_tool") or "<unknown tool>"
    filt = spec_dict.get("filter") or {}
    action = spec_dict.get("action_prompt") or ""
    lines = [
        f"Rule #{rule_id} saved.",
        f"- Checks `{source}` every {interval}s.",
        f"- Matches when: {filt}",
        f"- Action: {action}",
        f"- Auto-approve writes: {auto}",
    ]
    return "\n".join(lines)


def register(
    mcp: MCPManager,
    *,
    llm: Any,
    db_factory: async_sessionmaker[AsyncSession],
    scheduler: _SchedulerLike | None,
    default_model: str | None,
) -> None:
    """Attach `rules__create_rule` to the given MCPManager."""

    async def handler(arguments: dict[str, Any]) -> dict[str, Any]:
        description = arguments.get("description")
        if not isinstance(description, str) or not description.strip():
            return {
                "content": "description is required and must be non-empty",
                "is_error": True,
                "structured": None,
            }

        raw_interval = arguments.get("interval_seconds")
        try:
            interval_seconds = int(raw_interval)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return {
                "content": "interval_seconds must be an integer",
                "is_error": True,
                "structured": None,
            }
        if interval_seconds < 60:
            return {
                "content": "interval_seconds must be >= 60",
                "is_error": True,
                "structured": None,
            }

        auto_approve = bool(arguments.get("auto_approve", False))

        try:
            compiled = await compile_rule(
                description.strip(), mcp, llm, model=default_model
            )
        except RuleCompilationError as exc:
            return {
                "content": f"rule compilation failed: {exc}",
                "is_error": True,
                "structured": None,
            }

        compiled_json = compiled.model_dump_json()
        compiled_dict = compiled.model_dump()

        async with db_factory() as session:
            rule = Rule(
                description=description.strip(),
                compiled_spec=compiled_json,
                interval_seconds=interval_seconds,
                auto_approve=auto_approve,
                enabled=True,
            )
            session.add(rule)
            await session.commit()
            await session.refresh(rule)

            # Allocate the per-rule activity conversation in the same
            # transaction so the UI can link to it immediately.
            title_seed = rule.description.strip() or f"rule {rule.id}"
            activity = Conversation(
                title=f"Rule: {title_seed[:60]}",
                kind="rule_activity",
            )
            session.add(activity)
            await session.commit()
            await session.refresh(activity)
            rule.activity_conversation_id = activity.id
            session.add(rule)
            await session.commit()
            await session.refresh(rule)
            rule_id = rule.id

        if scheduler is not None:
            try:
                await scheduler.reload()
            except Exception:  # noqa: BLE001 - scheduler failure must not kill tool
                pass

        summary = _summarise(compiled_dict, rule_id or -1, interval_seconds, auto_approve)
        return {
            "content": summary,
            "is_error": False,
            "structured": {
                "id": rule_id,
                "compiled_spec": compiled_dict,
                "interval_seconds": interval_seconds,
                "auto_approve": auto_approve,
            },
        }

    mcp.register_builtin(
        server="rules",
        name="create_rule",
        description=(
            "Create a proactive rule: a background check that polls a "
            "source tool on an interval and, on match, performs an "
            "action for the user. Ask the user for interval_seconds "
            "(suggest 60/300/900/3600) and auto_approve before calling."
        ),
        parameters=RULE_CREATE_TOOL_SPEC,
        handler=handler,
    )


__all__ = ["RULE_CREATE_TOOL_SPEC", "register"]
