from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


_REDACTION_PATTERNS = (
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(r"(authorization|api[_-]?key|token|access_token|refresh_token|client_secret)\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"setupUrl\s*[:=]\s*\S+", re.IGNORECASE),
)


def _safe_error_excerpt(body: str | None, *, limit: int = 200) -> str:
    """Strip credential-like substrings from an upstream error body and
    truncate. Use whenever a Smithery response body bubbles into a
    persisted error message or SSE payload."""
    if not body:
        return ""
    redacted = body
    for pat in _REDACTION_PATTERNS:
        redacted = pat.sub("[REDACTED]", redacted)
    redacted = redacted.strip().replace("\n", " ")
    if len(redacted) > limit:
        redacted = redacted[: limit - 1] + "…"
    return redacted


@dataclass
class ConnectionStatus:
    state: str  # "connected" | "auth_required" | "input_required" | "error" | "unknown"
    setup_url: str | None = None
    error: str | None = None
    server_name: str | None = None
    server_version: str | None = None


class SmitheryError(RuntimeError):
    pass


class SmitheryClient:
    """Thin async wrapper over the Smithery Connect REST API.

    See https://smithery.ai/docs/use/connect for the protocol.
    """

    def __init__(
        self,
        *,
        api_key: str,
        namespace: str,
        api_base: str = "https://api.smithery.ai",
        timeout: float = 30.0,
    ):
        self._api_key = api_key
        self._namespace = namespace
        self._api_base = api_base.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _connection_url(self, connection_id: str) -> str:
        return f"{self._api_base}/connect/{self._namespace}/{connection_id}"

    @staticmethod
    def _parse_status(payload: dict[str, Any]) -> ConnectionStatus:
        status = payload.get("status") or {}
        info = payload.get("serverInfo") or {}
        return ConnectionStatus(
            state=str(status.get("state") or "unknown"),
            setup_url=status.get("setupUrl"),
            error=status.get("error") or status.get("message"),
            server_name=info.get("name"),
            server_version=info.get("version"),
        )

    async def upsert_connection(
        self,
        connection_id: str,
        *,
        mcp_url: str | None = None,
        name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ConnectionStatus:
        """PUT /connect/{namespace}/{connectionId} — creates or refreshes a connection.

        If `mcp_url` is omitted, this acts as a status check on an existing connection.
        Smithery returns the current status, including a `setupUrl` if OAuth is needed.
        """
        body: dict[str, Any] = {}
        if mcp_url:
            body["mcpUrl"] = mcp_url
        if name:
            body["name"] = name
        if metadata:
            body["metadata"] = metadata

        resp = await self._client.put(self._connection_url(connection_id), json=body)
        if resp.status_code >= 400:
            logger.debug("Smithery PUT %s → %s: %s", connection_id, resp.status_code, resp.text)
            raise SmitheryError(
                f"Smithery PUT {connection_id} → {resp.status_code}: {_safe_error_excerpt(resp.text)}"
            )
        return self._parse_status(resp.json())

    async def get_status(self, connection_id: str) -> ConnectionStatus | None:
        """Return the current status, or None if no connection exists yet."""
        resp = await self._client.get(self._connection_url(connection_id))
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            logger.debug("Smithery GET %s → %s: %s", connection_id, resp.status_code, resp.text)
            raise SmitheryError(
                f"Smithery GET {connection_id} → {resp.status_code}: {_safe_error_excerpt(resp.text)}"
            )
        return self._parse_status(resp.json())

    async def delete_connection(self, connection_id: str) -> None:
        resp = await self._client.delete(self._connection_url(connection_id))
        if resp.status_code not in (200, 204, 404):
            logger.debug("Smithery DELETE %s → %s: %s", connection_id, resp.status_code, resp.text)
            raise SmitheryError(
                f"Smithery DELETE {connection_id} → {resp.status_code}: {_safe_error_excerpt(resp.text)}"
            )

    async def jsonrpc(
        self,
        connection_id: str,
        *,
        method: str,
        params: dict[str, Any] | None = None,
        request_id: int = 1,
    ) -> dict[str, Any]:
        body = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        resp = await self._client.post(
            f"{self._connection_url(connection_id)}/mcp",
            json=body,
            headers={"Accept": "application/json, text/event-stream"},
        )
        if resp.status_code >= 400:
            logger.debug(
                "Smithery JSON-RPC %s on %s → %s: %s",
                method,
                connection_id,
                resp.status_code,
                resp.text,
            )
            raise SmitheryError(
                f"Smithery JSON-RPC {method} on {connection_id} → "
                f"{resp.status_code}: {_safe_error_excerpt(resp.text)}"
            )
        content_type = resp.headers.get("content-type", "").lower()
        if "text/event-stream" in content_type:
            payload = _extract_jsonrpc_from_sse(resp.text, request_id)
            if payload is None:
                raise SmitheryError(
                    f"No JSON-RPC response with id={request_id} in SSE body "
                    f"from {connection_id} ({method})"
                )
        else:
            payload = resp.json()
        if "error" in payload:
            err = payload["error"]
            raise SmitheryError(
                f"JSON-RPC error from {connection_id} ({method}): "
                f"{err.get('code')} {err.get('message')}"
            )
        return payload.get("result") or {}

    async def list_tools(self, connection_id: str) -> list[dict[str, Any]]:
        result = await self.jsonrpc(connection_id, method="tools/list")
        tools = result.get("tools") or []
        return [t for t in tools if isinstance(t, dict)]

    async def call_tool(
        self, connection_id: str, *, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        return await self.jsonrpc(
            connection_id,
            method="tools/call",
            params={"name": name, "arguments": arguments},
        )


def _extract_jsonrpc_from_sse(body: str, request_id: int) -> dict[str, Any] | None:
    """Extract the JSON-RPC response whose id matches `request_id` from an SSE body.

    MCP's streamable-HTTP transport may return `text/event-stream` where each
    event has one or more `data:` lines containing a JSON-RPC message. A server
    can stream progress notifications before the final response, so we scan all
    events and pick the one whose `id` matches our request.
    """
    for chunk in body.split("\n\n"):
        data_lines = [
            line[5:].lstrip()
            for line in chunk.splitlines()
            if line.startswith("data:")
        ]
        if not data_lines:
            continue
        try:
            payload = json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("id") == request_id:
            return payload
    return None
