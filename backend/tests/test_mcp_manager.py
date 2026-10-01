from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from mcp_manager import MCPManager, _Cache, _normalize_call_result
from smithery_client import ConnectionStatus, SmitheryError


def _make_mgr(tmp_path: Path, *, servers: dict[str, dict[str, Any]] | None = None,
              api_key: str = "smy-test-key") -> MCPManager:
    cfg = tmp_path / "integrations.json"
    cfg.write_text(
        json.dumps(
            {
                "namespace": "pm-assistant-test",
                "servers": servers
                or {
                    "jira": {
                        "label": "Jira",
                        "mcpUrl": "https://server.smithery.ai/@atlassian/mcp/mcp",
                    },
                    "github": {
                        "label": "GitHub",
                        "mcpUrl": "https://server.smithery.ai/@github/mcp/mcp",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    return MCPManager(
        cfg,
        smithery_api_key=api_key,
        smithery_namespace="pm-assistant-test",
    )


# ---------------------------------------------------------------------------
# Result normalization
# ---------------------------------------------------------------------------


class TestNormalizeCallResult:
    def test_concatenates_text_content(self) -> None:
        out = _normalize_call_result(
            {
                "content": [
                    {"type": "text", "text": "line1"},
                    {"type": "text", "text": "line2"},
                ],
                "isError": False,
            }
        )
        assert out == {"content": "line1\nline2", "is_error": False, "structured": None}

    def test_is_error_propagates(self) -> None:
        out = _normalize_call_result(
            {"content": [{"type": "text", "text": "boom"}], "isError": True}
        )
        assert out["is_error"] is True
        assert out["content"] == "boom"

    def test_structured_passthrough(self) -> None:
        out = _normalize_call_result(
            {
                "content": [{"type": "text", "text": "ok"}],
                "structuredContent": {"items": [1, 2, 3]},
            }
        )
        assert out["structured"] == {"items": [1, 2, 3]}

    def test_handles_no_content(self) -> None:
        out = _normalize_call_result({"isError": False})
        assert out == {"content": "", "is_error": False, "structured": None}


# ---------------------------------------------------------------------------
# list_all_tools — formats cached tools as OpenAI function-tool dicts
# ---------------------------------------------------------------------------


class TestListAllTools:
    def test_namespacing_and_format(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        # Skip start(); inject cache directly.
        mgr._integrations  # populated lazily by start
        mgr._load_config()
        mgr._cache["jira"] = _Cache(
            tools=[
                {
                    "name": "search_issues",
                    "description": "Search",
                    "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
                },
                {"name": "create_issue"},
            ]
        )
        mgr._cache["github"] = _Cache(
            tools=[{"name": "get_repo", "description": "Get"}]
        )

        out = mgr.list_all_tools()
        names = [t["function"]["name"] for t in out]
        assert names == ["jira__search_issues", "jira__create_issue", "github__get_repo"]

        first = out[0]
        assert first["type"] == "function"
        assert first["function"]["description"] == "Search"
        assert first["function"]["parameters"] == {
            "type": "object",
            "properties": {"q": {"type": "string"}},
        }

    def test_empty(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        mgr._load_config()
        assert mgr.list_all_tools() == []

    def test_missing_description_becomes_empty_string(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        mgr._load_config()
        mgr._cache["jira"] = _Cache(tools=[{"name": "x"}])
        out = mgr.list_all_tools()
        assert out[0]["function"]["description"] == ""
        assert out[0]["function"]["parameters"] == {"type": "object", "properties": {}}


# ---------------------------------------------------------------------------
# call_tool — dispatch through SmitheryClient
# ---------------------------------------------------------------------------


class TestCallTool:
    @pytest.mark.asyncio
    async def test_dispatch_to_correct_connection(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        mgr._load_config()
        mgr._smithery.call_tool = AsyncMock(  # type: ignore[method-assign]
            return_value={"content": [{"type": "text", "text": "jira-ok"}], "isError": False}
        )

        result = await mgr.call_tool("jira__search_issues", {"q": "bug"})

        assert result == {"content": "jira-ok", "is_error": False, "structured": None}
        mgr._smithery.call_tool.assert_awaited_once_with(
            "jira", name="search_issues", arguments={"q": "bug"}
        )

    @pytest.mark.asyncio
    async def test_split_on_first_double_underscore_only(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        mgr._load_config()
        mgr._smithery.call_tool = AsyncMock(  # type: ignore[method-assign]
            return_value={"content": [], "isError": False}
        )
        await mgr.call_tool("jira__foo__bar", {"x": 1})
        mgr._smithery.call_tool.assert_awaited_once_with(
            "jira", name="foo__bar", arguments={"x": 1}
        )

    @pytest.mark.asyncio
    async def test_unknown_integration_returns_error(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        mgr._load_config()
        result = await mgr.call_tool("ghost__do", {})
        assert result["is_error"] is True
        assert "ghost" in result["content"]

    @pytest.mark.asyncio
    async def test_invalid_qualified_name(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        mgr._load_config()
        result = await mgr.call_tool("no_double_underscore", {})
        assert result["is_error"] is True
        assert "invalid tool name" in result["content"]

    @pytest.mark.asyncio
    async def test_smithery_error_becomes_error_dict(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        mgr._load_config()
        mgr._smithery.call_tool = AsyncMock(  # type: ignore[method-assign]
            side_effect=SmitheryError("upstream 502")
        )
        result = await mgr.call_tool("jira__x", {})
        assert result["is_error"] is True
        assert "upstream 502" in result["content"]


# ---------------------------------------------------------------------------
# connect / refresh / disconnect lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_unconfigured_when_no_api_key(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path, api_key="")
        await mgr.start()
        views = mgr.list_integrations()
        assert {v.state for v in views} == {"unconfigured"}
        await mgr.stop()

    @pytest.mark.asyncio
    async def test_connect_returns_setup_url_for_oauth(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        mgr._load_config()
        mgr._smithery.upsert_connection = AsyncMock(  # type: ignore[method-assign]
            return_value=ConnectionStatus(
                state="auth_required",
                setup_url="https://smithery.ai/auth/jira/abc",
            )
        )
        view = await mgr.connect("jira")
        assert view.state == "auth_required"
        assert view.setup_url == "https://smithery.ai/auth/jira/abc"

    @pytest.mark.asyncio
    async def test_connect_then_loads_tools_when_connected(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        mgr._load_config()
        mgr._smithery.upsert_connection = AsyncMock(  # type: ignore[method-assign]
            return_value=ConnectionStatus(state="connected", server_name="jira")
        )
        mgr._smithery.list_tools = AsyncMock(  # type: ignore[method-assign]
            return_value=[{"name": "search_issues"}]
        )
        view = await mgr.connect("jira")
        assert view.state == "connected"
        assert view.tool_count == 1
        # Tools should now appear in list_all_tools()
        names = [t["function"]["name"] for t in mgr.list_all_tools()]
        assert "jira__search_issues" in names

    @pytest.mark.asyncio
    async def test_disconnect_clears_cache(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        mgr._load_config()
        mgr._cache["jira"] = _Cache(
            status=ConnectionStatus(state="connected"),
            tools=[{"name": "x"}],
        )
        mgr._smithery.delete_connection = AsyncMock(return_value=None)  # type: ignore[method-assign]
        view = await mgr.disconnect("jira")
        assert view.state == "disconnected"
        assert view.tool_count == 0

    @pytest.mark.asyncio
    async def test_unknown_integration_raises(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        mgr._load_config()
        with pytest.raises(KeyError):
            await mgr.connect("ghost")


# ---------------------------------------------------------------------------
# Config loading edge cases
# ---------------------------------------------------------------------------


class TestConfigLoading:
    @pytest.mark.asyncio
    async def test_missing_config_file_is_noop(self, tmp_path: Path) -> None:
        mgr = MCPManager(
            tmp_path / "missing.json",
            smithery_api_key="key",
            smithery_namespace="ns",
        )
        await mgr.start()
        assert mgr.list_integrations() == []
        await mgr.stop()

    @pytest.mark.asyncio
    async def test_invalid_json_is_noop(self, tmp_path: Path) -> None:
        cfg = tmp_path / "bad.json"
        cfg.write_text("{not valid", encoding="utf-8")
        mgr = MCPManager(
            cfg, smithery_api_key="key", smithery_namespace="ns"
        )
        await mgr.start()
        assert mgr.list_integrations() == []
        await mgr.stop()

    @pytest.mark.asyncio
    async def test_skips_entries_without_mcp_url(self, tmp_path: Path) -> None:
        cfg = tmp_path / "integrations.json"
        cfg.write_text(
            json.dumps(
                {
                    "servers": {
                        "good": {"label": "Good", "mcpUrl": "https://x"},
                        "bad": {"label": "Bad"},
                    }
                }
            ),
            encoding="utf-8",
        )
        mgr = MCPManager(
            cfg, smithery_api_key="", smithery_namespace="ns"
        )
        await mgr.start()
        names = {v.name for v in mgr.list_integrations()}
        assert names == {"good"}
        await mgr.stop()
