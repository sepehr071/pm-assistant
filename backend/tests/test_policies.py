from __future__ import annotations

import pytest

from agent.policies import (
    READ_TOKENS,
    READ_TOOL_OVERRIDES,
    WRITE_TOKENS,
    WRITE_TOOL_OVERRIDES,
    is_read_tool,
    is_write_tool,
    requires_approval,
)


class TestIsReadTool:
    @pytest.mark.parametrize(
        "name",
        [
            # Original four servers — coverage held from old prefix-based logic.
            "jira__search_issues",
            "jira__get_issue",
            "jira__list_projects",
            "jira__read_comment",
            "jira__find_users",
            "github__get_repo",
            "github__list_issues",
            "github__search_code",
            "github__view_pull",
            "github__read_file",
            "slack__get_channel",
            "slack__list_channels",
            "slack__search_messages",
            "slack__conversations_history",
            "notion__query_database",
            "notion__search_pages",
            # New servers — verb-based, no per-server allowlist needed.
            "linear__list_issues",
            "linear__search_issues",
            "googlecalendar__list_events",
            "gmail__list_messages",
            "gmail__search_messages",
            "confluence__search",
            "figma__get_file",
            # Verb buried later in the name still wins for read.
            "github__history",
            "slack__users_info",
        ],
    )
    def test_read_tools_identified(self, name: str) -> None:
        assert is_read_tool(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "jira__create_issue",
            "jira__update_issue",
            "jira__delete_comment",
            "jira__transition_issue",
            "github__create_pull",
            "github__merge_pr",
            "github__push_commit",
            "slack__post_message",
            "slack__send_dm",
            "notion__create_page",
            "notion__update_block",
            "linear__create_issue",
            "gmail__send_message",
            "googlecalendar__create_event",
            "figma__update_file",
        ],
    )
    def test_write_tools_identified(self, name: str) -> None:
        assert is_read_tool(name) is False
        assert is_write_tool(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            # Bypass attempts the old prefix-based gate would have allowed.
            "jira__get_or_create_issue",
            "jira__list_and_archive",
            "github__get_or_create_pull",
            "github__list_user_groups_users_update",
            "notion__read_and_post",
            "notion__search_and_create",
            "slack__get_then_delete_thread",
            "slack__list_user_groups_users_update",
            "linear__get_and_assign",
            "gmail__list_and_send",
        ],
    )
    def test_write_verb_wins_over_read_verb(self, name: str) -> None:
        """If any segment is a write verb, the tool must be classified
        as a write — even if the name also contains a read verb."""
        assert is_read_tool(name) is False
        assert is_write_tool(name) is True

    def test_unknown_server_with_read_verb_is_read(self) -> None:
        # Verb-based logic is server-agnostic — that's the whole point.
        assert is_read_tool("brand_new_server__search") is True
        assert is_read_tool("foo__list_things") is True

    def test_unknown_server_with_no_recognised_verb_defaults_to_write(self) -> None:
        # Default-deny: a name that doesn't match either tokenset is treated
        # as a write so the approval gate catches it.
        assert is_read_tool("unknown__do_thing") is False
        assert is_write_tool("unknown__do_thing") is True
        assert is_read_tool("unknown__foo") is False

    def test_malformed_name_treated_as_write(self) -> None:
        assert is_read_tool("no_double_underscore") is False
        assert is_write_tool("no_double_underscore") is True

    def test_empty_server_or_tool(self) -> None:
        assert is_read_tool("__tool") is False
        assert is_read_tool("server__") is False

    def test_case_insensitive_matching(self) -> None:
        assert is_read_tool("JIRA__SEARCH_issues") is True
        assert is_read_tool("Github__GetRepo") is False  # `getrepo` is one token, not a recognised verb
        assert is_read_tool("Github__Get_Repo") is True
        assert is_read_tool("JIRA__CREATE_issue") is False

    def test_split_on_first_double_underscore(self) -> None:
        # Tool name with extra "__" inside still classified by tokens.
        assert is_read_tool("slack__conversations_history") is True
        assert is_read_tool("jira__search__inner") is True
        assert is_read_tool("jira__create__inner") is False

    def test_overrides(self) -> None:
        # READ override pins something verb logic alone wouldn't catch.
        assert is_read_tool("github__me") is True
        # WRITE override pins a builtin tool that must always require approval.
        assert is_write_tool("rules__create_rule") is True
        assert is_read_tool("rules__create_rule") is False


class TestRequiresApproval:
    def test_write_tool_requires_approval(self) -> None:
        assert requires_approval("jira__create_issue", set()) is True

    def test_read_tool_does_not_require_approval(self) -> None:
        assert requires_approval("jira__search_issues", set()) is False

    def test_auto_approve_overrides_write(self) -> None:
        assert (
            requires_approval("jira__create_issue", {"jira__create_issue"}) is False
        )

    def test_auto_approve_exact_match_only(self) -> None:
        assert (
            requires_approval("jira__create_issue", {"jira__create_other"}) is True
        )

    def test_yolo_skips_approval_on_write(self) -> None:
        assert (
            requires_approval("jira__create_issue", set(), yolo=True) is False
        )

    def test_unknown_server_with_write_verb_requires_approval(self) -> None:
        assert requires_approval("unknown__delete_thing", set()) is True

    def test_unknown_server_unrecognised_verb_requires_approval(self) -> None:
        # Default-deny: novel servers can't bypass the gate by inventing verbs.
        assert requires_approval("unknown__do_thing", set()) is True


class TestTokenSets:
    def test_token_sets_disjoint(self) -> None:
        # A verb cannot be both read and write.
        assert WRITE_TOKENS.isdisjoint(READ_TOKENS)

    def test_overrides_contain_only_qualified_names(self) -> None:
        for name in READ_TOOL_OVERRIDES | WRITE_TOOL_OVERRIDES:
            assert "__" in name, f"override {name!r} must be a qualified name"
            server, tool = name.split("__", 1)
            assert server and tool

    def test_overrides_disjoint(self) -> None:
        assert READ_TOOL_OVERRIDES.isdisjoint(WRITE_TOOL_OVERRIDES)
