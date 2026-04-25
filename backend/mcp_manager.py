from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from smithery_client import ConnectionStatus, SmitheryClient, SmitheryError

BuiltinHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass
class _BuiltinTool:
    server: str
    name: str
    description: str
    parameters: dict[str, Any]
    handler: BuiltinHandler

    @property
    def qualified_name(self) -> str:
        return f"{self.server}__{self.name}"

logger = logging.getLogger(__name__)


@dataclass
class MCPServerStatus:
    name: str
    healthy: bool
    error: str | None = None
    tool_count: int = 0


@dataclass
class IntegrationConfig:
    name: str
    label: str
    mcp_url: str


@dataclass
class IntegrationView:
    name: str
    label: str
    state: str  # "connected" | "auth_required" | "input_required" | "error" | "disconnected" | "unconfigured"
    setup_url: str | None = None
    error: str | None = None
    tool_count: int = 0
    server_name: str | None = None


@dataclass
class _Cache:
    status: ConnectionStatus | None = None
    tools: list[dict[str, Any]] = field(default_factory=list)


class MCPManager:
    """Owns Smithery Connect connections for each configured integration.

    Public surface preserved from the old (stdio/streamable_http) version so
    `agent/loop.py` and `api/tools.py` keep working unchanged:
      - start() / stop()
      - list_all_tools() -> list[dict]
      - call_tool(qualified_name, args) -> dict
      - status() -> list[MCPServerStatus]

    New surface for the Integrations API:
      - list_integrations() -> list[IntegrationView]
      - connect(name) / refresh(name) / disconnect(name)
    """

    def __init__(
        self,
        config_path: Path,
        *,
        smithery_api_key: str,
        smithery_namespace: str,
        smithery_api_base: str = "https://api.smithery.ai",
    ):
        self._config_path = config_path
        self._integrations: dict[str, IntegrationConfig] = {}
        self._cache: dict[str, _Cache] = {}
        self._smithery = SmitheryClient(
            api_key=smithery_api_key,
            namespace=smithery_namespace,
            api_base=smithery_api_base,
        )
        self._lock = asyncio.Lock()
        self._started = False
        self._builtins: dict[str, _BuiltinTool] = {}

    # ------------------------------------------------------------------
    # builtin tools (locally-dispatched, no Smithery round-trip)
    # ------------------------------------------------------------------

    def register_builtin(
        self,
        *,
        server: str,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: BuiltinHandler,
    ) -> None:
        """Register a locally-dispatched tool exposed to the LLM as
        `{server}__{name}`. Overwrites any prior registration with the
        same qualified name."""
        tool = _BuiltinTool(
            server=server,
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
        )
        self._builtins[tool.qualified_name] = tool

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._started:
            return
        self._started = True

        self._load_config()

        if not self._smithery.configured:
            logger.warning(
                "SMITHERY_API_KEY is not set; integrations will be disabled "
                "until you add it and restart."
            )
            return

        # Best-effort: fan out a status check for each integration so the UI
        # can immediately show which ones are already connected.
        await asyncio.gather(
            *(self._refresh_locked(name) for name in self._integrations),
            return_exceptions=True,
        )

    async def stop(self) -> None:
        await self._smithery.aclose()
        self._cache.clear()
        self._integrations.clear()
        self._started = False

    def _load_config(self) -> None:
        try:
            raw = json.loads(Path(self._config_path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            logger.warning(
                "Integrations config not found at %s; no integrations loaded",
                self._config_path,
            )
            return
        except json.JSONDecodeError as exc:
            logger.error(
                "Invalid integrations config JSON at %s: %s",
                self._config_path,
                exc,
            )
            return

        if not isinstance(raw, dict):
            logger.error(
                "Integrations config must be a JSON object, got %s",
                type(raw).__name__,
            )
            return

        servers = raw.get("servers")
        if not isinstance(servers, dict):
            logger.error("Integrations config missing top-level 'servers' object")
            return

        for name, cfg in servers.items():
            if not isinstance(cfg, dict):
                logger.warning("Skipping integration %s: not an object", name)
                continue
            mcp_url = cfg.get("mcpUrl")
            if not isinstance(mcp_url, str) or not mcp_url:
                logger.warning("Skipping integration %s: missing mcpUrl", name)
                continue
            label = str(cfg.get("label") or name)
            self._integrations[name] = IntegrationConfig(
                name=name, label=label, mcp_url=mcp_url
            )
            self._cache.setdefault(name, _Cache())

    # ------------------------------------------------------------------
    # tool dispatch (used by agent loop)
    # ------------------------------------------------------------------

    def list_all_tools(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for tool in self._builtins.values():
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.qualified_name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
            )
        for name, cache in self._cache.items():
            for tool in cache.tools:
                tool_name = tool.get("name")
                if not isinstance(tool_name, str):
                    continue
                out.append(
                    {
                        "type": "function",
                        "function": {
                            "name": f"{name}__{tool_name}",
                            "description": tool.get("description") or "",
                            "parameters": tool.get("inputSchema")
                            or {"type": "object", "properties": {}},
                        },
                    }
                )
        return out

    async def call_tool(
        self, qualified_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        if "__" not in qualified_name:
            return {
                "content": f"invalid tool name: {qualified_name!r} (missing server prefix)",
                "is_error": True,
                "structured": None,
            }

        builtin = self._builtins.get(qualified_name)
        if builtin is not None:
            try:
                result = await builtin.handler(arguments)
            except Exception as exc:
                logger.exception("Builtin tool %s failed", qualified_name)
                return {
                    "content": f"{type(exc).__name__}: {exc}",
                    "is_error": True,
                    "structured": None,
                }
            content = result.get("content")
            if not isinstance(content, str):
                content = json.dumps(content, default=str)
            return {
                "content": content,
                "is_error": bool(result.get("is_error")),
                "structured": result.get("structured"),
            }

        server_name, tool_name = qualified_name.split("__", 1)
        if server_name not in self._integrations:
            return {
                "content": f"unknown integration: {server_name!r}",
                "is_error": True,
                "structured": None,
            }

        try:
            raw_result = await self._smithery.call_tool(
                server_name, name=tool_name, arguments=arguments
            )
        except SmitheryError as exc:
            logger.exception("Smithery tool call failed: %s", qualified_name)
            return {
                "content": str(exc),
                "is_error": True,
                "structured": None,
            }

        normalized = _normalize_call_result(raw_result)
        if _looks_like_auth_failure(normalized):
            # Upstream says the token is dead. Flip our cache to
            # `auth_required` so the system prompt the LLM sees on the
            # next turn reflects reality, and drop cached agent
            # sessions so the prompt actually rebuilds. The frontend
            # polls the integrations endpoint, so the red state
            # surfaces to the user within seconds.
            await self._flag_auth_required(server_name, reason=normalized["content"])
        return normalized

    async def _flag_auth_required(self, name: str, *, reason: str) -> None:
        """Mark an integration as auth-dead after a tool call revealed
        revoked credentials. Safe to call repeatedly — the update is
        idempotent when the cache is already in that state."""
        async with self._lock:
            cache = self._cache.get(name)
            if cache is not None and cache.status is not None and cache.status.state == "auth_required":
                return  # already flagged; nothing to do
            truncated = (reason or "").strip().splitlines()[0][:200]
            self._cache[name] = _Cache(
                status=ConnectionStatus(
                    state="auth_required",
                    error=f"Upstream rejected credentials: {truncated}",
                    server_name=(cache.status.server_name if cache and cache.status else None),
                )
            )
        # Drop cached AgentSessions so the next turn rebuilds the system
        # prompt against the fresh CAPABILITIES table. Local import
        # avoids a circular dependency at module load time.
        try:
            from api.stream import _reset_sessions

            _reset_sessions()
        except Exception:
            pass
        logger.warning(
            "Integration %s flagged auth_required after tool error: %s", name, reason
        )

    # ------------------------------------------------------------------
    # legacy /api/mcp/status surface (per-server health)
    # ------------------------------------------------------------------

    def status(self) -> list[MCPServerStatus]:
        out: list[MCPServerStatus] = []
        for name, cfg in self._integrations.items():
            cache = self._cache.get(name) or _Cache()
            status = cache.status
            healthy = bool(status and status.state == "connected")
            error: str | None = None
            if status is None:
                error = "not connected"
            elif status.state != "connected":
                error = status.error or status.state
            out.append(
                MCPServerStatus(
                    name=cfg.label,
                    healthy=healthy,
                    error=error,
                    tool_count=len(cache.tools),
                )
            )
        return out

    # ------------------------------------------------------------------
    # integrations API surface
    # ------------------------------------------------------------------

    def list_integrations(self) -> list[IntegrationView]:
        return [self._view(name) for name in self._integrations]

    def get_integration(self, name: str) -> IntegrationView | None:
        if name not in self._integrations:
            return None
        return self._view(name)

    def _view(self, name: str) -> IntegrationView:
        cfg = self._integrations[name]
        cache = self._cache.get(name) or _Cache()
        if not self._smithery.configured:
            return IntegrationView(
                name=name,
                label=cfg.label,
                state="unconfigured",
                error="SMITHERY_API_KEY is not set",
            )
        if cache.status is None:
            return IntegrationView(
                name=name, label=cfg.label, state="disconnected"
            )
        return IntegrationView(
            name=name,
            label=cfg.label,
            state=cache.status.state,
            setup_url=cache.status.setup_url,
            error=cache.status.error,
            tool_count=len(cache.tools),
            server_name=cache.status.server_name,
        )

    async def connect(self, name: str) -> IntegrationView:
        """Initiate (or refresh) a Smithery connection for `name`.

        Returns the latest view, including a `setup_url` the frontend should
        open in a popup if `state == "auth_required"`.
        """
        cfg = self._require(name)
        if not self._smithery.configured:
            return self._view(name)
        async with self._lock:
            try:
                status = await self._smithery.upsert_connection(
                    name,
                    mcp_url=cfg.mcp_url,
                    name=cfg.label,
                )
            except SmitheryError as exc:
                logger.exception("Smithery connect failed for %s", name)
                self._cache[name] = _Cache(
                    status=ConnectionStatus(state="error", error=str(exc))
                )
                return self._view(name)
            await self._apply_status_locked(name, status)
        return self._view(name)

    async def refresh(self, name: str) -> IntegrationView:
        """Re-check Smithery for the current state and refresh cached tools."""
        self._require(name)
        if not self._smithery.configured:
            return self._view(name)
        async with self._lock:
            await self._refresh_locked(name)
        return self._view(name)

    async def disconnect(self, name: str) -> IntegrationView:
        self._require(name)
        if not self._smithery.configured:
            return self._view(name)
        async with self._lock:
            try:
                await self._smithery.delete_connection(name)
            except SmitheryError as exc:
                logger.exception("Smithery disconnect failed for %s", name)
                self._cache[name] = _Cache(
                    status=ConnectionStatus(state="error", error=str(exc))
                )
                return self._view(name)
            self._cache[name] = _Cache()
        return self._view(name)

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    def _require(self, name: str) -> IntegrationConfig:
        cfg = self._integrations.get(name)
        if cfg is None:
            raise KeyError(f"unknown integration: {name!r}")
        return cfg

    async def _refresh_locked(self, name: str) -> None:
        try:
            status = await self._smithery.get_status(name)
        except SmitheryError as exc:
            logger.warning("Smithery status check failed for %s: %s", name, exc)
            self._cache[name] = _Cache(
                status=ConnectionStatus(state="error", error=str(exc))
            )
            return
        if status is None:
            self._cache[name] = _Cache()
            return
        await self._apply_status_locked(name, status)

    async def _apply_status_locked(
        self, name: str, status: ConnectionStatus
    ) -> None:
        cache = _Cache(status=status)
        if status.state == "connected":
            try:
                cache.tools = await self._smithery.list_tools(name)
            except SmitheryError as exc:
                logger.warning("Smithery tools/list failed for %s: %s", name, exc)
                cache.tools = []
        self._cache[name] = cache


def _normalize_call_result(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert a Smithery JSON-RPC tools/call result into our internal shape."""
    is_error = bool(raw.get("isError"))
    content = raw.get("content") or []
    text_parts: list[str] = []
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str):
                text_parts.append(text)
    return {
        "content": "\n".join(text_parts),
        "is_error": is_error,
        "structured": raw.get("structuredContent"),
    }


# Upstream error strings that mean "the OAuth token we hold is dead".
# Smithery's local connection state can lag reality — e.g. Slack returns
# `token_revoked` from `conversations.list` while Smithery still reports
# `state: connected` because its own probe (get_status) hasn't failed.
# When we see these, flip the cached integration state to `auth_required`
# so the next system prompt the LLM sees reflects the truth and the user
# gets a reconnect prompt in the UI.
_AUTH_FAILURE_MARKERS: tuple[str, ...] = (
    "token_revoked",
    "invalid_auth",
    "account_inactive",
    "not_authed",
    "token_expired",
    "missing_scope",
    "no_permission",
)


def _looks_like_auth_failure(result: dict[str, Any]) -> bool:
    """Return True when a tool result's content matches a known auth-dead
    marker from the upstream service (Slack, GitHub, Jira, Notion)."""
    content = result.get("content") or ""
    if not isinstance(content, str) or not content:
        return False
    lowered = content.lower()
    return any(marker in lowered for marker in _AUTH_FAILURE_MARKERS)
