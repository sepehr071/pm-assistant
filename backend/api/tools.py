from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api", tags=["tools"])


def _status_to_dict(status_obj: Any) -> dict[str, Any]:
    if isinstance(status_obj, dict):
        return dict(status_obj)
    # pydantic models (v2) → model_dump; dataclasses → __dict__
    dump = getattr(status_obj, "model_dump", None)
    if callable(dump):
        try:
            return dump()
        except Exception:
            pass
    if hasattr(status_obj, "__dict__"):
        return dict(vars(status_obj))
    # fall back to attribute probing
    result: dict[str, Any] = {}
    for attr in ("name", "status", "healthy", "error", "transport"):
        val = getattr(status_obj, attr, None)
        if val is not None:
            result[attr] = val
    return result


def _tool_to_dict(tool: Any) -> dict[str, Any]:
    if isinstance(tool, dict):
        # OpenAI-format {"type":"function","function":{"name":..,"description":..,"parameters":..}}
        fn = tool.get("function") if "function" in tool else tool
        return {
            "name": fn.get("name"),
            "description": fn.get("description", ""),
            "parameters": fn.get("parameters", {}),
        }
    # pydantic / object with attrs
    name = getattr(tool, "name", None)
    description = getattr(tool, "description", "") or ""
    parameters = (
        getattr(tool, "parameters", None)
        or getattr(tool, "input_schema", None)
        or getattr(tool, "inputSchema", None)
        or {}
    )
    return {"name": name, "description": description, "parameters": parameters}


def _server_from_tool_name(tool_name: str) -> str | None:
    if not tool_name or "__" not in tool_name:
        return None
    return tool_name.split("__", 1)[0]


def _bare_tool_name(tool_name: str) -> str:
    if "__" in tool_name:
        return tool_name.split("__", 1)[1]
    return tool_name


@router.get("/tools")
async def list_tools(request: Request) -> dict[str, Any]:
    mcp = getattr(request.app.state, "mcp", None)
    if mcp is None:
        return {"servers": {}}

    all_tools = []
    list_fn = getattr(mcp, "list_all_tools", None)
    if callable(list_fn):
        try:
            all_tools = list_fn() or []
        except Exception:
            all_tools = []

    status_entries: list[Any] = []
    status_fn = getattr(mcp, "status", None)
    if callable(status_fn):
        try:
            status_entries = status_fn() or []
        except Exception:
            status_entries = []

    servers: dict[str, dict[str, Any]] = {}
    for entry in status_entries:
        info = _status_to_dict(entry)
        name = info.get("name")
        if not name:
            continue
        servers[name] = {
            "status": info.get("status") or ("healthy" if info.get("healthy") else "unknown"),
            "tools": [],
        }

    for raw_tool in all_tools:
        normalized = _tool_to_dict(raw_tool)
        full_name = normalized.get("name") or ""
        server_name = _server_from_tool_name(full_name) or "_default"
        bucket = servers.setdefault(server_name, {"status": "unknown", "tools": []})
        bucket["tools"].append(
            {
                "name": _bare_tool_name(full_name),
                "description": normalized.get("description", ""),
                "parameters": normalized.get("parameters", {}),
            }
        )

    return {"servers": servers}


@router.get("/mcp/status")
async def mcp_status(request: Request) -> list[dict[str, Any]]:
    mcp = getattr(request.app.state, "mcp", None)
    if mcp is None:
        return []
    status_fn = getattr(mcp, "status", None)
    if not callable(status_fn):
        return []
    try:
        entries = status_fn() or []
    except Exception:
        return []
    return [_status_to_dict(e) for e in entries]
