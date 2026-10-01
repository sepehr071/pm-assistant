from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


def _mcp_or_503(request: Request):
    mcp = getattr(request.app.state, "mcp", None)
    if mcp is None:
        raise HTTPException(status_code=503, detail="MCP manager not initialized")
    return mcp


def _serialize(view: Any) -> dict[str, Any]:
    if hasattr(view, "__dataclass_fields__"):
        return asdict(view)
    if isinstance(view, dict):
        return view
    return dict(vars(view))


def _state_of(view: Any) -> str | None:
    """Extract the connection state field from an integration view."""
    if view is None:
        return None
    state = getattr(view, "state", None)
    if state is None and isinstance(view, dict):
        state = view.get("state")
    return str(state) if state is not None else None


def _invalidate_sessions_on_state_change(before: str | None, after: str | None) -> None:
    """Drop cached AgentSessions so the next turn rebuilds the system
    prompt with a fresh CAPABILITIES table. Sessions cache that table
    at creation — if an integration was in ``auth_required`` when a
    session was built, the LLM will keep seeing it as disconnected
    across every subsequent turn (web or Telegram) even after the user
    reconnects. Frontend polls ``/refresh`` every ~2s during OAuth, so
    only reset on an actual state transition to avoid thrashing live
    turns during the OAuth dance.
    """
    if before == after:
        return
    try:
        from api.stream import _reset_sessions  # local import avoids cycles

        _reset_sessions()
    except Exception:
        pass


@router.get("")
async def list_integrations(request: Request) -> dict[str, list[dict[str, Any]]]:
    mcp = _mcp_or_503(request)
    items = mcp.list_integrations() if hasattr(mcp, "list_integrations") else []
    return {"integrations": [_serialize(v) for v in items]}


@router.get("/{name}")
async def get_integration(request: Request, name: str) -> dict[str, Any]:
    mcp = _mcp_or_503(request)
    view = mcp.get_integration(name) if hasattr(mcp, "get_integration") else None
    if view is None:
        raise HTTPException(status_code=404, detail=f"unknown integration: {name}")
    return _serialize(view)


@router.post("/{name}/connect")
async def connect_integration(request: Request, name: str) -> dict[str, Any]:
    mcp = _mcp_or_503(request)
    before = _state_of(mcp.get_integration(name) if hasattr(mcp, "get_integration") else None)
    try:
        view = await mcp.connect(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown integration: {name}")
    _invalidate_sessions_on_state_change(before, _state_of(view))
    return _serialize(view)


@router.post("/{name}/refresh")
async def refresh_integration(request: Request, name: str) -> dict[str, Any]:
    mcp = _mcp_or_503(request)
    before = _state_of(mcp.get_integration(name) if hasattr(mcp, "get_integration") else None)
    try:
        view = await mcp.refresh(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown integration: {name}")
    _invalidate_sessions_on_state_change(before, _state_of(view))
    return _serialize(view)


@router.post("/{name}/disconnect")
async def disconnect_integration(request: Request, name: str) -> dict[str, Any]:
    mcp = _mcp_or_503(request)
    before = _state_of(mcp.get_integration(name) if hasattr(mcp, "get_integration") else None)
    try:
        view = await mcp.disconnect(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown integration: {name}")
    _invalidate_sessions_on_state_change(before, _state_of(view))
    return _serialize(view)


@router.post("/{name}/reconnect")
async def reconnect_integration(request: Request, name: str) -> dict[str, Any]:
    """One-shot recovery: revoke Smithery's stored tokens for this
    integration and immediately open a fresh connection so the returned
    ``setupUrl`` points at a clean OAuth flow.

    Needed because Smithery caches tokens even when the upstream service
    (Slack, GitHub, …) has revoked them. A plain ``connect`` against a
    stale connection id reuses the bad credentials; we must delete the
    connection first.
    """
    mcp = _mcp_or_503(request)
    before = _state_of(mcp.get_integration(name) if hasattr(mcp, "get_integration") else None)
    try:
        await mcp.disconnect(name)
        view = await mcp.connect(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown integration: {name}")
    _invalidate_sessions_on_state_change(before, _state_of(view))
    return _serialize(view)
