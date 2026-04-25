from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from services.rule_compiler import (
    CompiledSpec,
    RuleCompilationError,
    compile_rule,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeMessage:
    content: str | None


@dataclass
class _FakeChoice:
    message: _FakeMessage


@dataclass
class _FakeCompletion:
    choices: list[_FakeChoice]


class FakeLLM:
    """Scripted LLM whose `chat()` returns pre-canned JSON strings."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> _FakeCompletion:
        self.calls.append(
            {"model": model, "messages": list(messages), "tools": tools, "kwargs": dict(kwargs)}
        )
        if not self._replies:
            raise AssertionError("FakeLLM ran out of scripted replies")
        reply = self._replies.pop(0)
        return _FakeCompletion(choices=[_FakeChoice(message=_FakeMessage(content=reply))])


class FakeMcp:
    """Static MCP double with `list_all_tools()` returning a fixed list."""

    def __init__(self, tools: list[dict[str, Any]] | None = None) -> None:
        self._tools = tools if tools is not None else _DEFAULT_TOOLS

    def list_all_tools(self) -> list[dict[str, Any]]:
        return list(self._tools)


def _tool(name: str, description: str = "") -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {}},
        },
    }


_DEFAULT_TOOLS: list[dict[str, Any]] = [
    _tool("slack__conversations_history", "Fetch recent messages from a DM or channel."),
    _tool("slack__post_message", "Post a message to a channel or DM."),
    _tool("github__list_issues", "List issues in a repository."),
    _tool("github__list_pull_requests", "List pull requests in a repository."),
    _tool("github__list_notifications", "List notifications for the authenticated user."),
    _tool("jira__search_issues", "JQL search over Jira issues."),
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_slack_dm_match_pattern_compiles() -> None:
    reply = json.dumps(
        {
            "source_tool": "slack__conversations_history",
            "source_args": {"channel": "D0123", "limit": 20},
            "filter": {
                "kind": "message_from_user_contains",
                "user": "Sepehr",
                "contains": ["roadmap"],
            },
            "action_prompt": "Reply in thread: I'll review the roadmap today.",
        }
    )
    llm = FakeLLM([reply])
    mcp = FakeMcp()

    spec = await compile_rule(
        "Whenever Sepehr messages me in Slack about the roadmap, reply that I'll check today",
        mcp,
        llm,
        model="test/model",
    )

    assert isinstance(spec, CompiledSpec)
    assert spec.source_tool == "slack__conversations_history"
    assert spec.source_args == {"channel": "D0123", "limit": 20}
    assert spec.filter.kind == "message_from_user_contains"
    assert spec.filter.user == "Sepehr"
    assert spec.filter.contains == ["roadmap"]
    assert "roadmap" in spec.action_prompt.lower()
    # single LLM call, no retry needed
    assert len(llm.calls) == 1
    assert llm.calls[0]["model"] == "test/model"


async def test_github_pr_review_request_compiles() -> None:
    reply = json.dumps(
        {
            "source_tool": "github__list_pull_requests",
            "source_args": {"state": "open"},
            "filter": {
                "kind": "new_pr_review_request",
                "mention": "sepehr-radmard",
                "repo": "acme/api",
            },
            "action_prompt": "Summarize the PR diff and post a reminder in #eng.",
        }
    )
    llm = FakeLLM([reply])
    mcp = FakeMcp()

    spec = await compile_rule(
        "Tell me whenever I'm requested as a reviewer on a PR in acme/api",
        mcp,
        llm,
    )

    assert spec.source_tool == "github__list_pull_requests"
    assert spec.filter.kind == "new_pr_review_request"
    assert spec.filter.mention == "sepehr-radmard"
    assert spec.filter.repo == "acme/api"


async def test_github_issue_mention_compiles() -> None:
    reply = json.dumps(
        {
            "source_tool": "github__list_notifications",
            "source_args": {"reason": "mention"},
            "filter": {
                "kind": "new_issue_mentions",
                "mention": "sepehr-radmard",
            },
            "action_prompt": "Draft a short acknowledgement comment.",
        }
    )
    llm = FakeLLM([reply])
    mcp = FakeMcp()

    spec = await compile_rule(
        "Notify me when someone @-mentions me in a GitHub issue",
        mcp,
        llm,
    )

    assert spec.filter.kind == "new_issue_mentions"
    assert spec.filter.mention == "sepehr-radmard"
    assert spec.filter.repo is None


async def test_pure_schedule_compiles() -> None:
    reply = json.dumps(
        {
            "source_tool": "slack__conversations_history",
            "source_args": {"channel": "C_ENG"},
            "filter": {
                "kind": "schedule_only",
                "schedule": "every Monday 09:00",
            },
            "action_prompt": "Post a weekly status summary to #eng.",
        }
    )
    llm = FakeLLM([reply])
    mcp = FakeMcp()

    spec = await compile_rule(
        "Every Monday at 9am, send a weekly summary to the engineering channel",
        mcp,
        llm,
    )

    assert spec.filter.kind == "schedule_only"
    assert spec.filter.schedule == "every Monday 09:00"
    assert spec.filter.user is None
    assert spec.filter.contains is None


async def test_ambiguous_input_raises_after_two_attempts() -> None:
    # Both attempts return an explicit refusal; second attempt should also fail
    # and the compiler must raise RuleCompilationError.
    refusal = json.dumps({"error": "description is too vague"})
    llm = FakeLLM([refusal, refusal])
    mcp = FakeMcp()

    with pytest.raises(RuleCompilationError) as excinfo:
        await compile_rule("do the thing", mcp, llm)

    # retry happened
    assert len(llm.calls) == 2
    assert "vague" in str(excinfo.value) or "could not compile" in str(excinfo.value)


async def test_invalid_json_then_valid_json_succeeds_on_retry() -> None:
    # First reply is not JSON; compiler should retry and succeed with the second.
    bad = "sure, here you go: not-json-at-all"
    good = json.dumps(
        {
            "source_tool": "slack__conversations_history",
            "source_args": {"channel": "D0123"},
            "filter": {
                "kind": "message_from_user_contains",
                "user": "Sepehr",
                "contains": ["ping"],
            },
            "action_prompt": "Reply: pong.",
        }
    )
    llm = FakeLLM([bad, good])
    mcp = FakeMcp()

    spec = await compile_rule("when Sepehr slacks me 'ping', reply 'pong'", mcp, llm)
    assert spec.filter.contains == ["ping"]
    assert len(llm.calls) == 2
    # retry note must have been injected into the second call's system prompt
    second_system = llm.calls[1]["messages"][0]["content"]
    assert "RETRY" in second_system or "Attempt 1 failed" in second_system


async def test_missing_required_filter_field_raises() -> None:
    # LLM returns a message_from_user_contains filter without `contains` — both
    # attempts will fail validation.
    bad = json.dumps(
        {
            "source_tool": "slack__conversations_history",
            "source_args": {},
            "filter": {"kind": "message_from_user_contains", "user": "Sepehr"},
            "action_prompt": "Reply.",
        }
    )
    llm = FakeLLM([bad, bad])
    mcp = FakeMcp()

    with pytest.raises(RuleCompilationError):
        await compile_rule("when Sepehr messages me something", mcp, llm)
