from __future__ import annotations

from agent.system_prompt import build_system_prompt
from mcp_manager import IntegrationView


class _FakeMcp:
    def __init__(self, integrations: list[IntegrationView] | None = None) -> None:
        self._integrations = integrations or []

    def list_integrations(self) -> list[IntegrationView]:
        return list(self._integrations)


def test_base_prompt_present_with_no_integrations() -> None:
    prompt = build_system_prompt(_FakeMcp(), user_extension=None)

    assert "PM Assistant" in prompt
    assert "Smithery" in prompt
    assert "{integration}__{tool}" in prompt
    # Approval gate language must be there.
    assert "approve" in prompt.lower()
    # No-integrations branch.
    assert "No integrations are currently configured" in prompt
    # User extension absent.
    assert "Additional instructions" not in prompt


def test_capabilities_lists_each_integration_with_state_and_tools() -> None:
    integrations = [
        IntegrationView(name="jira", label="Jira", state="connected", tool_count=12),
        IntegrationView(name="github", label="GitHub", state="connected", tool_count=8),
        IntegrationView(name="slack", label="Slack", state="auth_required", tool_count=0),
        IntegrationView(name="notion", label="Notion", state="disconnected", tool_count=0),
    ]
    prompt = build_system_prompt(_FakeMcp(integrations), user_extension=None)

    # Every integration label appears.
    for label in ("Jira", "GitHub", "Slack", "Notion"):
        assert label in prompt

    # The state translations land in the table.
    assert "needs sign-in" in prompt
    assert "not connected" in prompt
    assert "connected" in prompt

    # Tool counts shown.
    assert "12" in prompt
    assert "8" in prompt

    # The "only connected can be called" guidance is present (some are connected).
    assert "Only the integrations marked **connected**" in prompt


def test_no_connected_message_when_nothing_is_connected() -> None:
    integrations = [
        IntegrationView(name="jira", label="Jira", state="auth_required", tool_count=0),
    ]
    prompt = build_system_prompt(_FakeMcp(integrations), user_extension=None)

    assert "No integration is connected yet" in prompt


def test_user_extension_appended_under_heading_when_provided() -> None:
    prompt = build_system_prompt(
        _FakeMcp(),
        user_extension="Always reply in haiku form.",
    )

    assert "## Additional instructions" in prompt
    assert "Always reply in haiku form." in prompt
    # Base content must still be present alongside the extension.
    assert "PM Assistant" in prompt


def test_user_extension_blank_or_whitespace_is_ignored() -> None:
    prompt = build_system_prompt(_FakeMcp(), user_extension="   \n  ")
    assert "Additional instructions" not in prompt


def test_yolo_mode_is_announced() -> None:
    prompt = build_system_prompt(_FakeMcp(), user_extension=None, yolo_mode=True)
    assert "YOLO mode is ON" in prompt

    prompt_off = build_system_prompt(_FakeMcp(), user_extension=None, yolo_mode=False)
    assert "YOLO mode is ON" not in prompt_off


def test_falls_back_gracefully_when_mcp_raises() -> None:
    class _Boom:
        def list_integrations(self) -> list[IntegrationView]:
            raise RuntimeError("smithery is down")

    prompt = build_system_prompt(_Boom(), user_extension=None)
    # Should still produce the base + the empty-capabilities branch.
    assert "PM Assistant" in prompt
    assert "No integrations are currently configured" in prompt
