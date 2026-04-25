"""Natural-language → structured rule spec compiler.

Given a short user description (e.g. "reply 'pong' whenever Sepehr DMs me
the word test-rule on Slack") this module calls the LLM once with JSON-mode
enabled and returns a `CompiledSpec` — the shared contract consumed by the
rule engine + rule scheduler.

Design:
- `CompiledFilter` is intentionally a **flat** pydantic model keyed by
  `kind: Literal[...]` with optional fields per kind. A discriminated union
  was considered but every consumer has to branch on `kind` anyway and a
  flat model keeps the JSON schema (and the LLM output) dead simple.
- On a JSON-parse or validation failure the compiler retries **once** with
  the parser error appended to the system prompt; on a second failure it
  raises `RuleCompilationError` with a human-readable message.
"""
from __future__ import annotations

import json
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, ValidationError


# ---------------------------------------------------------------------------
# Public contract
# ---------------------------------------------------------------------------


FilterKind = Literal[
    "message_from_user_contains",
    "new_issue_mentions",
    "new_pr_review_request",
    "schedule_only",
]


class CompiledFilter(BaseModel):
    """Closed-set filter descriptor.

    Flat model: every kind lives in the same class with its own optional
    fields. Consumers switch on `kind`. Keep required-per-kind invariants
    in `CompiledSpec._validate_filter` (called after construction) rather
    than at field level, because pydantic's `Field` can't express "required
    if kind == X".
    """

    kind: FilterKind
    # message_from_user_contains
    user: str | None = None
    contains: list[str] | None = None
    # new_issue_mentions / new_pr_review_request
    mention: str | None = None  # github handle / display name to watch for
    repo: str | None = None  # optional "owner/name" scope
    # schedule_only
    schedule: str | None = None  # cron expression or human hint echoed back by LLM


class CompiledSpec(BaseModel):
    source_tool: str = Field(
        ...,
        description="Qualified MCP tool name, e.g. slack__conversations_history",
    )
    source_args: dict[str, Any] = Field(default_factory=dict)
    filter: CompiledFilter
    action_prompt: str = Field(
        ...,
        description="Natural-language instruction the agent executes on match.",
    )


class RuleCompilationError(Exception):
    """Raised when the LLM output cannot be mapped onto `CompiledSpec`."""


# ---------------------------------------------------------------------------
# LLM / MCP duck-typed protocols (kept loose so tests can inject fakes).
# ---------------------------------------------------------------------------


class _LLMLike(Protocol):
    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Any: ...


class _McpLike(Protocol):
    def list_all_tools(self) -> list[dict[str, Any]]: ...


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


_FILTER_GUIDE = """\
Filter kinds (`filter.kind`):

1. "message_from_user_contains" — a chat message from a specific user
   whose text contains any of the listed substrings (case-insensitive).
   Required fields: `user` (display name or handle), `contains` (list of
   substrings, at least one). Use for Slack DM / channel watchers.

2. "new_issue_mentions" — a new or updated issue that @-mentions the
   target user. Required fields: `mention` (handle or display name).
   Optional: `repo` ("owner/name") for scoping. Use for GitHub/Jira
   issue-mention watchers.

3. "new_pr_review_request" — a pull request where the target user has
   been requested as a reviewer. Required fields: `mention` (handle).
   Optional: `repo`. Use for GitHub PR review-request watchers.

4. "schedule_only" — no content filter; fire on every tick. Required
   field: `schedule` (a human-readable echo, e.g. "every Monday 09:00").
   The scheduler's interval_seconds owns the actual cadence — this field
   is informational only.
"""


_RESPONSE_SHAPE = """\
Respond with a single JSON object and nothing else. Shape:

{
  "source_tool": "<integration>__<tool>",
  "source_args": { ... },
  "filter": {
    "kind": "<one of the four kinds above>",
    "user": "...",           // for message_from_user_contains
    "contains": ["..."],     // for message_from_user_contains
    "mention": "...",        // for new_issue_mentions / new_pr_review_request
    "repo": "owner/name",    // optional, same kinds
    "schedule": "..."        // for schedule_only
  },
  "action_prompt": "Natural-language instruction the agent should follow on match."
}

Omit filter fields that do not apply to the chosen kind. Use null or omit,
never guess. If the user's description is too vague to pick a
`source_tool` or a `filter.kind`, respond with exactly:

{"error": "<short reason>"}
"""


def _summarize_schema(schema: Any) -> str:
    """Render a JSON-schema parameters block compactly for the prompt.

    Emits one line per property: `name (type[, required][, enum=[...]]) — desc`.
    Falls back to a raw JSON dump when the shape isn't the expected
    object-with-properties form.
    """
    if not isinstance(schema, dict):
        return ""
    props = schema.get("properties")
    if not isinstance(props, dict) or not props:
        # No properties to describe; if there's anything else interesting dump it.
        keys = [k for k in schema.keys() if k not in ("type",)]
        if not keys:
            return "    (no arguments)"
        return "    " + json.dumps(schema, ensure_ascii=False)[:400]

    required = set(schema.get("required") or [])
    lines: list[str] = []
    for pname, pschema in props.items():
        if not isinstance(pschema, dict):
            lines.append(f"    - {pname}")
            continue
        ptype = pschema.get("type") or ("enum" if pschema.get("enum") else "any")
        if isinstance(ptype, list):
            ptype = "|".join(str(t) for t in ptype)
        parts = [str(ptype)]
        if pname in required:
            parts.append("required")
        enum_vals = pschema.get("enum")
        if isinstance(enum_vals, list) and enum_vals:
            rendered = ",".join(json.dumps(v, ensure_ascii=False) for v in enum_vals[:8])
            if len(enum_vals) > 8:
                rendered += ",…"
            parts.append(f"enum=[{rendered}]")
        default = pschema.get("default")
        if default is not None:
            parts.append(f"default={json.dumps(default, ensure_ascii=False)}")
        desc = (pschema.get("description") or "").strip().splitlines()
        desc_str = desc[0][:160] if desc else ""
        head = f"    - {pname} ({', '.join(parts)})"
        lines.append(f"{head} — {desc_str}" if desc_str else head)
    return "\n".join(lines)


def _format_tool_list(tools: list[dict[str, Any]]) -> str:
    if not tools:
        return "(no tools available — no integrations connected)"
    blocks: list[str] = []
    for spec in tools:
        fn = spec.get("function") or {}
        name = fn.get("name") or spec.get("name")
        if not name:
            continue
        desc = (fn.get("description") or spec.get("description") or "").strip()
        first_line = desc.splitlines()[0][:200] if desc else ""
        params = fn.get("parameters") or spec.get("parameters") or {}
        schema_block = _summarize_schema(params)
        header = f"- `{name}`"
        if first_line:
            header += f" — {first_line}"
        blocks.append(f"{header}\n{schema_block}" if schema_block else header)
    return "\n".join(blocks) if blocks else "(no tools available)"


def _build_system_prompt(tools: list[dict[str, Any]], *, extra_note: str | None = None) -> str:
    tool_list = _format_tool_list(tools)
    extra = f"\n\nIMPORTANT RETRY NOTE:\n{extra_note.strip()}\n" if extra_note else ""
    return (
        "You compile a user's natural-language automation rule into a structured "
        "JSON spec. The spec is then executed by a background scheduler that polls "
        "one MCP tool, filters the result with a closed-set predicate, and runs a "
        "short agent turn with the `action_prompt` when the predicate matches.\n\n"
        "Available MCP tools (pick exactly one for `source_tool` — it MUST be a "
        "polling / read-style tool such as `*_history`, `*_list`, `*_search`, "
        "`*_notifications`, never a write). Each tool is followed by its "
        "input-parameter schema — `source_args` MUST satisfy that schema: "
        "include every `required` field, only use listed property names, and "
        "respect declared `enum` / `type` / `default` constraints:\n"
        f"{tool_list}\n\n"
        f"{_FILTER_GUIDE}\n"
        f"{_RESPONSE_SHAPE}"
        f"{extra}"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _extract_message_content(response: Any) -> str:
    """Pull the assistant text out of whatever the LLM returned.

    Handles both the real `openai.types.chat.ChatCompletion` object AND a
    dict-shaped fake, so the compiler is testable without the SDK.
    """
    # dict-shaped (fake)
    if isinstance(response, dict):
        choices = response.get("choices") or []
        if not choices:
            return ""
        msg = choices[0].get("message") or {}
        return msg.get("content") or ""
    # object-shaped (real openai SDK or dataclass fake)
    choices = getattr(response, "choices", None) or []
    if not choices:
        return ""
    choice = choices[0]
    msg = getattr(choice, "message", None)
    if msg is None:
        return ""
    content = getattr(msg, "content", None)
    return content or ""


def _parse_spec(raw_text: str) -> CompiledSpec:
    """Parse raw LLM output → CompiledSpec or raise a descriptive exception."""
    text = (raw_text or "").strip()
    if not text:
        raise ValueError("LLM returned empty content")

    # Tolerate a ```json fenced block if the model hedges.
    if text.startswith("```"):
        text = text.strip("`")
        # strip an optional leading "json\n"
        if text.lower().startswith("json"):
            text = text[4:].lstrip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON from LLM: {exc.msg}") from exc

    if not isinstance(data, dict):
        raise ValueError("LLM output is not a JSON object")

    # Explicit "I can't" signal from the model.
    if "error" in data and "source_tool" not in data:
        raise ValueError(f"LLM refused: {data.get('error') or 'unspecified reason'}")

    try:
        spec = CompiledSpec.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"spec failed schema validation: {exc}") from exc

    _validate_filter_invariants(spec.filter)
    _validate_source_tool(spec.source_tool)
    return spec


def _validate_filter_invariants(f: CompiledFilter) -> None:
    """Enforce required-per-kind fields that pydantic can't express declaratively."""
    if f.kind == "message_from_user_contains":
        if not f.user or not f.contains:
            raise ValueError(
                "filter.kind=message_from_user_contains requires `user` and non-empty `contains`"
            )
    elif f.kind in ("new_issue_mentions", "new_pr_review_request"):
        if not f.mention:
            raise ValueError(f"filter.kind={f.kind} requires `mention`")
    elif f.kind == "schedule_only":
        if not f.schedule:
            raise ValueError("filter.kind=schedule_only requires `schedule`")


def _validate_source_tool(name: str) -> None:
    if "__" not in name:
        raise ValueError(
            f"source_tool {name!r} is not namespaced — must be `<integration>__<tool>`"
        )


async def compile_rule(
    description: str,
    mcp: _McpLike,
    llm: _LLMLike,
    model: str | None = None,
) -> CompiledSpec:
    """Compile a natural-language rule description into a `CompiledSpec`.

    Retries once on parse/validation failure, appending the error to the
    system prompt so the model can self-correct. Second failure raises
    `RuleCompilationError`.
    """
    desc = (description or "").strip()
    if not desc:
        raise RuleCompilationError("rule description is empty")

    # Resolve model lazily (tests can pass `model` directly so we don't need to
    # import config here unless the caller omitted it).
    effective_model = model
    if effective_model is None:
        try:
            from config import get_settings  # local import: avoid settings during tests
            effective_model = get_settings().openrouter_default_model
        except Exception:
            effective_model = "google/gemini-3-flash-preview"

    tools = list(mcp.list_all_tools() or [])
    last_error: str | None = None

    for attempt in range(2):
        system_prompt = _build_system_prompt(tools, extra_note=last_error)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": desc},
        ]
        try:
            response = await llm.chat(
                model=effective_model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.2,
                extra_body={"provider": {"sort": "latency"}},
            )
        except Exception as exc:
            # LLM transport error — surface cleanly, no second attempt is going
            # to help if the network is down.
            raise RuleCompilationError(f"LLM call failed: {exc}") from exc

        raw = _extract_message_content(response)
        try:
            return _parse_spec(raw)
        except ValueError as exc:
            last_error = (
                f"Attempt {attempt + 1} failed: {exc}. "
                f"The previous response was:\n{raw[:600]}\n"
                "Return a valid JSON object matching the schema exactly."
            )
            continue

    raise RuleCompilationError(
        f"could not compile rule after 2 attempts — {last_error or 'unknown error'}"
    )
