"""Approval-gate classifier for MCP tool calls.

Default-deny semantics. A tool is treated as a *write* (requires
explicit user approval) unless it satisfies BOTH:

1. None of the underscore-separated segments of the tool name match a
   write verb (`create`, `update`, `delete`, `post`, `send`, `merge`,
   `close`, `archive`, `write`, `upsert`, `patch`, `put`, `set`, …).
2. At least one segment matches a known read verb (`get`, `list`,
   `search`, `read`, `find`, `view`, `query`, `fetch`, `history`, …).

This kills the previous prefix-only bypasses such as
`get_or_create_issue`, `read_and_post`, `list_user_groups_users_update`
where a write verb was hidden after a read prefix. Server name is no
longer part of the decision: third-party MCP servers added later
(Linear, Gmail, Confluence, …) get the same treatment as the original
four with no per-server config.

Exact-match overrides (`READ_TOOL_OVERRIDES`, `WRITE_TOOL_OVERRIDES`)
let us pin individual tools when the verb heuristic is wrong.
"""

from __future__ import annotations

# Verb segments that always classify a tool as a write. Listed in
# alphabetical order; combined whole-word match against
# underscore-separated segments only — substring matches don't count.
WRITE_TOKENS: frozenset[str] = frozenset(
    {
        "add",
        "append",
        "approve",
        "archive",
        "assign",
        "ban",
        "block",
        "cancel",
        "clear",
        "close",
        "complete",
        "create",
        "delete",
        "deploy",
        "disable",
        "dismiss",
        "edit",
        "enable",
        "execute",
        "fork",
        "import",
        "insert",
        "install",
        "invite",
        "kick",
        "lock",
        "merge",
        "move",
        "mute",
        "open",  # opens an issue/PR — write
        "patch",
        "pin",
        "post",
        "publish",
        "purge",
        "push",
        "put",
        "rebase",
        "register",
        "reject",
        "release",
        "remove",
        "rename",
        "reopen",
        "reply",
        "request",  # request_review etc create state
        "reset",
        "resolve",  # resolve_thread / resolve_issue
        "restart",
        "restore",
        "revoke",
        "run",  # run_workflow / run_pipeline
        "save",
        "schedule",
        "send",
        "set",
        "share",
        "start",
        "stop",
        "subscribe",
        "sync",
        "transfer",
        "transition",
        "trigger",
        "unarchive",
        "unblock",
        "unlock",
        "unmute",
        "unpin",
        "unsubscribe",
        "update",
        "upload",
        "upsert",
        "withdraw",
        "write",
    }
)


# Verb segments that classify a tool as a read when none of the
# WRITE_TOKENS are present.
READ_TOKENS: frozenset[str] = frozenset(
    {
        "browse",
        "check",
        "count",
        "describe",
        "diff",
        "discover",
        "download",  # idempotent on the remote server
        "enumerate",
        "explain",
        "explore",
        "export",
        "fetch",
        "find",
        "get",
        "head",
        "history",
        "info",
        "inspect",
        "introspect",
        "list",
        "load",
        "lookup",
        "ls",
        "match",
        "me",
        "members",
        "ping",
        "preview",
        "query",
        "read",
        "resolve_id",  # tool-name idiom: lookup an id; combined-word, see overrides
        "retrieve",
        "scan",
        "search",
        "show",
        "stat",
        "stats",
        "status",
        "summary",
        "tail",
        "view",
        "whoami",
    }
)


# Exact qualified-name overrides. Use sparingly — verb logic should
# cover ~all real cases. Keys are full `{server}__{tool}` strings.
READ_TOOL_OVERRIDES: frozenset[str] = frozenset(
    {
        # Slack legacy method names where neither half is a generic verb.
        "slack__conversations_history",
        "slack__conversations_replies",
        "slack__conversations_members",
        "slack__conversations_info",
        "slack__users_info",
        "slack__users_lookupByEmail",
        "slack__team_info",
        # GitHub: octokit-flavored tool names.
        "github__me",
        "github__rate_limit",
        # Generic "auth check" style.
        "rules__list",
    }
)


WRITE_TOOL_OVERRIDES: frozenset[str] = frozenset(
    {
        # Builtin in-process tool: persists a Rule row + reloads scheduler.
        "rules__create_rule",
    }
)


def _split(qualified_name: str) -> tuple[str, str] | None:
    if "__" not in qualified_name:
        return None
    server, tool = qualified_name.split("__", 1)
    if not server or not tool:
        return None
    return server, tool


def _segments(tool: str) -> list[str]:
    """Lowercased, underscore-split segments. Empty segments dropped so
    `search__inner` (an oddball with double-underscore inside) still
    yields meaningful tokens."""
    return [s for s in tool.lower().split("_") if s]


def is_read_tool(qualified_name: str) -> bool:
    """True only when the tool is unambiguously a read.

    Decision order:
      1. Malformed name / empty server-or-tool → False (treat as write).
      2. Exact override hit → use the override.
      3. Any segment in WRITE_TOKENS → False (write wins, even if the
         name also contains a read verb like `list` or `get`).
      4. Any segment in READ_TOKENS → True.
      5. Otherwise → False (default-deny).
    """
    parts = _split(qualified_name)
    if parts is None:
        return False
    qname = qualified_name.lower()
    if qname in WRITE_TOOL_OVERRIDES:
        return False
    if qname in READ_TOOL_OVERRIDES:
        return True
    _, tool = parts
    segs = _segments(tool)
    if not segs:
        return False
    if any(s in WRITE_TOKENS for s in segs):
        return False
    return any(s in READ_TOKENS for s in segs)


def is_write_tool(qualified_name: str) -> bool:
    """Inverse of `is_read_tool` for the well-formed name path; for a
    malformed name (no `__`) we still classify as write so the gate
    catches it rather than silently passing."""
    return not is_read_tool(qualified_name)


def requires_approval(
    qualified_name: str,
    auto_approve: set[str],
    *,
    yolo: bool = False,
) -> bool:
    if yolo:
        return False
    if qualified_name in auto_approve:
        return False
    return is_write_tool(qualified_name)
