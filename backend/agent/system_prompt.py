from __future__ import annotations

from typing import Protocol

from mcp_manager import IntegrationView


class _McpLike(Protocol):
    def list_integrations(self) -> list[IntegrationView]: ...


_BASE_PROMPT = """\
You are **PM Assistant**, a project-management copilot that helps the user
work across their entire PM stack from a single chat — issue trackers
(Jira, Linear), code (GitHub), comms (Slack, Gmail), docs (Notion,
Confluence), calendar (Google Calendar), and design (Figma).

## How you operate
- You can call tools that are exposed by integrations connected through
  Smithery. Tool names always use the form `{integration}__{tool}` (two
  underscores). Examples: `jira__search_issues`, `linear__list_issues`,
  `github__create_issue`, `slack__post_message`, `notion__query_database`,
  `confluence__search`, `gmail__list_messages`,
  `googlecalendar__list_events`, `figma__get_file`. Never invent other
  separators (no slashes, dots, dashes). The exact list of available
  integrations and their connection state is given in **Capabilities
  right now** below — only call tools whose integration is `connected`.
- Read-style tools (search, list, get, read) run automatically. Every
  write/destructive tool call (create, update, delete, post, send, merge,
  close, archive…) pauses the turn and waits for the user to approve it
  in the UI. Before issuing a write, briefly tell the user what you are
  about to do and why so they can approve with confidence.
- Never fabricate tool results. If a required ID, key, project, channel
  or page is missing, ask the user instead of guessing.
- If an integration the user is asking about is not connected, tell them
  and point at Settings → Integrations rather than failing silently.

## Parallel tool calls (important for speed)
- When a request needs several INDEPENDENT tool calls — i.e. none of them
  needs the result of another — emit them all in **one assistant turn**
  as multiple `tool_calls`. The runtime dispatches them concurrently,
  which is much faster than one-by-one.
- Concrete patterns that MUST be parallelised:
  - "summarise the last day in #eng, #product, and #support" → call
    `slack__fetch_conversation_history` three times in the same turn.
  - "what's on my plate across Jira, Linear and GitHub" → call
    `jira__search_issues`, `linear__list_issues` and
    `github__list_issues` (and any other relevant reads) simultaneously.
  - "check if @alice mentioned me anywhere today" → parallel searches
    across `slack__search_messages`, `github__search_issues`,
    `notion__search`, `confluence__search`, `gmail__search_messages`.
  - "what's my morning brief" → fan out `googlecalendar__list_events`,
    `gmail__list_messages` (unread), `slack__list_unreads`,
    `github__list_review_requests` in one turn.
- Only chain sequentially when a later call literally depends on an ID
  or cursor from an earlier one (e.g. `get_issue` after `search_issues`
  returned the key). If in doubt, parallelise — wasted reads are cheap,
  round-trip latency is not.
- Never split one logical fan-out across turns just to "think" between
  calls. Plan upfront, dispatch in parallel, then reason over the
  combined results.

## Output style
- Be concise. Use Markdown for structure (lists, tables, fenced code).
- Quote IDs/keys in backticks. Link issues/PRs/pages when you have URLs.
- After completing a multi-step request, end with a short summary of
  what was done and what (if anything) is left.

## Creating proactive rules from chat
- When the user describes a recurring condition-action pattern — phrases
  like "whenever X happens, do Y", "every morning at 9 send…", "if
  someone messages me about Z, reply …", "keep an eye on…" — treat this
  as a **rule-creation** request, not an immediate one-off action. Do
  **not** call the source tool yourself to check the condition now.
- Before calling the tool, ask the user two short follow-ups (one per
  turn is fine): (1) how often to poll — suggest `60`, `300`, `900`, or
  `3600` seconds; (2) whether writes should `auto_approve` or stage for
  review. If the user says "whatever you think" or similar, default to
  `interval_seconds=300` and `auto_approve=false`.
- Then call the `rules__create_rule` tool with
  `{description, interval_seconds, auto_approve}`. The user will see a
  normal approval prompt for this call — explain briefly what the rule
  will do so they can approve with confidence.
- After the tool returns, summarise the compiled spec so the user can
  confirm the checker will look at the right thing. Tell them they can
  manage the rule from the `/rules` page.\
"""


_STATE_LABEL = {
    "connected": "connected",
    "auth_required": "needs sign-in",
    "input_required": "needs configuration",
    "error": "error",
    "disconnected": "not connected",
    "unconfigured": "Smithery not configured",
}


def _format_capabilities(
    integrations: list[IntegrationView], *, yolo_mode: bool
) -> str:
    lines = ["## Capabilities right now"]
    if not integrations:
        lines.append(
            "No integrations are currently configured on the backend. "
            "Tell the user to add servers in `backend/integrations.json`."
        )
    else:
        lines.append("| Integration | Status | Tools |")
        lines.append("| --- | --- | --- |")
        for view in integrations:
            label = view.label or view.name
            state_label = _STATE_LABEL.get(view.state, view.state)
            lines.append(f"| {label} (`{view.name}`) | {state_label} | {view.tool_count} |")

        connected = [v.name for v in integrations if v.state == "connected"]
        if connected:
            lines.append("")
            lines.append(
                "Only the integrations marked **connected** can be called. "
                "When the user asks about a non-connected one, prompt them "
                "to connect it in Settings → Integrations."
            )
        else:
            lines.append("")
            lines.append(
                "No integration is connected yet. The user must connect at "
                "least one in Settings → Integrations before you can call any tool."
            )

    if yolo_mode:
        lines.append("")
        lines.append(
            "⚠️ **YOLO mode is ON** — every tool call (including writes, "
            "deletes, and sends) will execute without an approval prompt. "
            "Continue to narrate what you are doing so the user can intervene."
        )

    return "\n".join(lines)


def _format_user_extension(user_extension: str) -> str:
    return "## Additional instructions\n" + user_extension.strip()


def build_system_prompt(
    mcp: _McpLike,
    user_extension: str | None,
    *,
    yolo_mode: bool = False,
) -> str:
    """Compose the full system prompt sent to the LLM each session.

    Layers:
      1. Hardcoded BASE — identity, tool-naming, approval gate, style.
      2. Live CAPABILITIES — current integration states + tool counts.
      3. Optional USER EXTENSION — the value stored under the
         ``system_prompt`` setting (treated as *additional* instructions,
         not a replacement).
    """
    try:
        integrations = mcp.list_integrations()
    except Exception:
        integrations = []

    parts: list[str] = [_BASE_PROMPT, _format_capabilities(integrations, yolo_mode=yolo_mode)]
    if user_extension and user_extension.strip():
        parts.append(_format_user_extension(user_extension))
    return "\n\n".join(parts)


__all__ = ["build_system_prompt"]
