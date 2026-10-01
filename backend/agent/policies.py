"""Approval-gate classifier for MCP tool calls.

Default-deny semantics. A tool is treated as a *write* (requires
explicit user approval) unless it satisfies BOTH:

1. None of the name's word segments match a write verb (`create`,
   `update`, `delete`, `post`, `send`, `merge`, `close`, `archive`,
   `write`, `upsert`, `patch`, `put`, `set`, …).
2. At least one segment matches a known read verb (`get`, `list`,
   `search`, `read`, `find`, `view`, `query`, `fetch`, `history`, …).

Segments are produced by splitting on underscores **and** camelCase /
PascalCase boundaries (`chat_postMessage` → [chat, post, message]), so a
verb hidden inside a method-style name like `slack__chat_postMessage` or
`gmail__sendEmail` still surfaces as its own token. Without this the
camelCase blob (`postmessage`) never matched a single-word verb and such
tools slipped the gate.

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

import re

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


# Verbs considered destructive / irreversible / externally-visible. When
# the `dangerous_always_approve` setting is on, a write whose segments hit
# one of these stays gated even with YOLO enabled — protects against the
# model dropping data, sending things to outside parties, mutating
# resources, or moving things you can't easily undo. Checked only after a
# tool is already classified as a write, so noun forms of these words on a
# read tool (`read_comment`, `get_comment`) never trip the guard.
# `comment` lives here but NOT in WRITE_TOKENS for exactly that reason.
DANGEROUS_TOKENS: frozenset[str] = frozenset(
    {
        "assign",
        "ban",
        "cancel",
        "clear",
        "close",
        "comment",
        "delete",
        "deploy",
        "edit",
        "kick",
        "merge",
        "move",
        "post",
        "publish",
        "purge",
        "release",
        "remove",
        "rename",
        "reply",
        "reset",
        "revoke",
        "send",
        "set",
        "share",
        "transfer",
        "update",
        "upload",
        "withdraw",
        "write",
    }
)


# Exact qualified-name tools that should always be treated as
# dangerous, even if their verb segments don't match DANGEROUS_TOKENS.
DANGEROUS_TOOL_OVERRIDES: frozenset[str] = frozenset(set())


def is_dangerous_tool(qualified_name: str) -> bool:
    """True when the tool is a destructive or externally-visible write.

    Reuses the same segment-tokenisation as `is_read_tool` so naming
    quirks (`get_or_delete_X`, `list_and_remove`) are handled consistently.
    Read-classified tools are never dangerous.
    """
    parts = _split(qualified_name)
    if parts is None:
        return False
    qname = qualified_name.lower()
    if qname in DANGEROUS_TOOL_OVERRIDES:
        return True
    if is_read_tool(qualified_name):
        return False
    _, tool = parts
    segs = _segments(tool)
    return any(s in DANGEROUS_TOKENS for s in segs)


def _split(qualified_name: str) -> tuple[str, str] | None:
    if "__" not in qualified_name:
        return None
    server, tool = qualified_name.split("__", 1)
    if not server or not tool:
        return None
    return server, tool


# Split a single underscore-delimited chunk into camelCase / PascalCase
# words. Handles acronym runs (`getHTTPServer` -> get, http, server),
# trailing acronyms (`exportPDF` -> export, pdf), and digit groups.
_CAMEL_WORD = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")


def _segments(tool: str) -> list[str]:
    """Lowercased word segments of a tool name.

    Splits first on underscores, then on camelCase / PascalCase
    boundaries, so a verb buried in a method-style name surfaces as its
    own token: `chat_postMessage` -> [chat, post, message],
    `users_lookupByEmail` -> [users, lookup, by, email]. Without this the
    dangerous/write classifiers only saw the whole camelCase blob
    (`postmessage`) and never matched a single-word verb token.

    Empty chunks from doubled underscores are dropped so the oddball
    `search__inner` still yields meaningful tokens. A chunk with no
    matchable word (shouldn't normally happen) falls back to its own
    lowercased self so it is never silently lost.
    """
    segs: list[str] = []
    for chunk in tool.split("_"):
        if not chunk:
            continue
        words = _CAMEL_WORD.findall(chunk)
        if words:
            segs.extend(w.lower() for w in words)
        else:
            segs.append(chunk.lower())
    return segs


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
    dangerous_always_approve: bool = True,
) -> bool:
    # The dangerous-always-approve guard wins over both YOLO and the
    # per-tool allow list — it exists precisely to keep destructive
    # writes prompting even when the user has otherwise opted out.
    if dangerous_always_approve and is_dangerous_tool(qualified_name):
        return True
    if yolo:
        return False
    if qualified_name in auto_approve:
        return False
    return is_write_tool(qualified_name)
