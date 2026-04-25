# CLAUDE.md

This file guide Claude Code (claude.ai/code) when work with code in this repo.

## What this is

PM Assistant — local-first chat UI. Stream LLM responses (OpenRouter), execute tool calls against third-party services (Jira, GitHub, Slack, Notion) via **Smithery Connect** as MCP proxy. Every write-scoped tool call gated on explicit user approval in UI before dispatch.

On top of reactive chat, app also **proactive**: users describe rules in natural language ("whenever X DMs me on Slack about Y, reply Z"). In-process `APScheduler` polls trigger on interval, runs action through `AgentSession` against pinned "Rules activity" system chat. See **Proactive rules** below.

## Repo layout

- `backend/` — Python 3.13 + FastAPI, async SQLite via SQLModel, `openai` SDK → OpenRouter, `httpx` → Smithery. Own `.git` (nested repo).
- `frontend/` — React 19 + Vite 8 + TypeScript + Tailwind v4 + Zustand + TanStack Query 5.
- `backend/integrations.json` — declares Smithery namespace, one entry per integration (`mcpUrl` → hosted MCP server on `server.smithery.ai`). Edit this file = only way to add/remove integrations.
- `.env` lives at **repo root**, not under `backend/` — `backend/config.py` loads `../.env`.

## Running (two terminals)

```bash
# backend (port 8000)
cd backend && uv run uvicorn main:app --reload --port 8000

# frontend (port 5173, proxies /api and /sse to :8000 via vite.config.ts)
cd frontend && pnpm dev
```

Install: `uv sync` (backend), `pnpm install` (frontend). Do **not** use pip/npm — lockfiles are `uv.lock` and `pnpm-lock.yaml`.

Required env vars (`.env` at repo root): `OPENROUTER_API_KEY`, `SMITHERY_API_KEY`. Optional: `OPENROUTER_DEFAULT_MODEL` (default `google/gemini-3-flash-preview`), `SMITHERY_NAMESPACE` (default `pm-assistant`), `DATABASE_URL`.

## Tests / lint

```bash
cd backend  && uv run pytest -v              # all
cd backend  && uv run pytest tests/test_agent_loop.py::test_approval_flow -v   # single test
cd frontend && pnpm test                      # vitest (jsdom)
cd frontend && pnpm lint                      # eslint
cd frontend && pnpm build                     # tsc -b && vite build (also typechecks)
```

Pytest configured with `asyncio_mode = "auto"` (pyproject.toml) — every `async def test_…` runs as coroutine, no `@pytest.mark.asyncio` needed. `respx` mocks httpx calls to Smithery. Current suite: **110 tests**.

## Architecture — things you can't see by grepping

### Agent loop (backend/agent/loop.py)

`AgentSession` orchestrates one chat turn: stream OpenRouter → assemble tool-call fragments by `index` → for each tool call, consult `agent/policies.py` → if write, pause + emit `tool_call_request`; if read (allowlisted verb per server) or auto-approved, dispatch immediately → emit `tool_call_result` → feed back into LLM. Bounded to ~20 iterations. Sessions held in-memory in `api/stream.py`'s `_sessions` dict keyed by `chat_id` — that how `POST /api/chats/{chat_id}/approve` endpoint finds paused session.

### System prompt assembly (backend/agent/system_prompt.py)

System prompt **always sent**, built per session in `api/stream.py:_get_or_create_session` via `build_system_prompt(mcp, user_extension, yolo_mode=…)`. Three pieces composed in order:

1. **BASE** — hardcoded identity, `{integration}__{tool}` naming convention, approval-gate semantics, output-style rules.
2. **CAPABILITIES** — markdown table generated from `mcp.list_integrations()` showing each integration's connection state + tool count. Notes if YOLO on.
3. **EXTENSION** — value stored in `UserSetting["system_prompt"]`, rendered under `## Additional instructions` only if non-empty. **Settings UI labels this field "Additional instructions"** — *appended* to BASE+CAPABILITIES, not replacement. Don't reintroduce old "system prompt override" semantics.

Pure function over `_McpLike` protocol — unit-tested in `tests/test_system_prompt.py` with fake MCP. `UserSetting` storage shape unchanged; assembly happens at API layer.

### SSE event contract (backend → frontend)

Stream emits five event types; frontend parses them in `frontend/src/lib/stream.ts`:

| event | payload | frontend handler |
|---|---|---|
| `token` | `{delta}` | `updateLastAssistantDelta` |
| `tool_call_request` | `{tool_call_id, name, arguments}` | push into `pendingApprovals[chatId]` |
| `tool_call_result` | `{tool_call_id, content}` | `addToolCallMessage` |
| `done` | `{message_id}` | `finalizeStreamingMessage` |
| `error` | `{message}` | surface toast |

Any new event type needs handlers on **both** sides — frontend drops unknown events silently.

### Settings changes drop sessions

`PATCH /api/settings` clears `_sessions` so next message POST rebuilds `AgentSession` with new `yolo_mode` / `auto_approve_tools` / model. Don't reuse session after settings mutation.

### Tool namespacing

Tools exposed to LLM as `{integration}__{tool}` (double underscore). `mcp_manager.call_tool(qualified_name, args)` splits on that separator to route to right Smithery connection. Don't rename integrations in `integrations.json` casually — stored conversation history references old names.

### Proactive rules (scheduler + builtin tool)

Rules subsystem lets user install recurring condition→action jobs without leaving product.

**Data model** (`backend/db/models.py`):
- `Rule` — `description` (NL), `compiled_spec` (JSON string), `interval_seconds` (≥ 60), `auto_approve`, `enabled`, `state_cursor` (last-seen marker so reruns don't double-fire), `last_run_at`, `last_error`.
- `RuleFiring` — one row per tick: `status ∈ {matched, no_match, error, pending_approval, expired, rejected}`, optional `message_id` into activity chat.
- `Conversation.kind` — `"user"` (default) or `"system_rules_activity"`. Exactly one row of latter seeded from `main.py` lifespan (`_seed_rules_activity_chat`).

**Compiler** (`backend/services/rule_compiler.py`): one OpenRouter call with `response_format={"type": "json_object"}`. Retries **once** with parser error appended to system prompt, then raises `RuleCompilationError`. `CompiledSpec.filter` is **flat** pydantic model keyed by `kind ∈ {message_from_user_contains, new_issue_mentions, new_pr_review_request, schedule_only}` — not discriminated union (every consumer branches on `kind` anyway). Per-kind required fields enforced imperatively in `_validate_filter_invariants`.

**Engine** (`backend/services/rule_engine.py::tick`): (1) call `source_tool` via `mcp.call_tool` — no LLM, no approval, always read-class; (2) apply filter matcher; (3) if match, build `AgentSession` against activity chat, seed with `"[rule trigger] <action_prompt>. Context: <match summary>"`, run one turn; (4) `auto_approve=True` → `yolo_mode=True`; `auto_approve=False` → session stored in `api/stream.py::_sessions` under activity chat id so existing `/approve` endpoint works unchanged; (5) update `rule.state_cursor`; (6) all errors caught, written to `rule.last_error` + `RuleFiring(status="error")` row. **Hot path zero LLM calls when nothing matches.** Module-level error counter auto-disables rule after 5 consecutive failures.

**Scheduler** (`backend/services/rule_scheduler.py`): `AsyncIOScheduler` with `max_instances=1`, `misfire_grace_time=interval_seconds`. `RuleScheduler.reload()` diffs `rule` table against job table — call after any POST/PATCH/DELETE on `/api/rules` or after builtin tool persists new rule. Boot wrapped in try/except in `main.py` so API stays up even if scheduler fails (`app.state.scheduler = None`).

**Two ways to create rule:**

1. **Manual** — `/rules` page → "New rule" → two-step `RuleEditor` (describe → review compiled spec → save). Goes through `POST /api/rules` which compiles + persists + calls `scheduler.reload()`.

2. **Chat-driven** — builtin `rules__create_rule` tool registered on `MCPManager` via `services/rule_tool.py::register()` (called from `main.py` lifespan after scheduler boot). LLM instructed by `agent/system_prompt.py`'s BASE to:
   - Detect NL rule intent ("whenever…", "every morning…", "if someone messages me about…").
   - Ask user inline for `interval_seconds` (suggest 60/300/900/3600) + `auto_approve` before calling — default 300 / false if user waves it on.
   - Call `rules__create_rule` — tool runs existing approval gate (unknown tool → treated as write by `agent/policies.py`), compiles, persists, reloads scheduler.

**Builtin tool registry on `MCPManager`** — `register_builtin(server, name, description, parameters, handler)` adds locally-dispatched tool. `list_all_tools()` prepends builtins; `call_tool()` intercepts `qualified_name` in `self._builtins` before Smithery path. Handler return shape: `{"content": str, "is_error": bool, "structured": Any}`. Use this pattern for any future in-process tools (export data, toggle settings, etc.) — don't bolt tools onto `agent/loop.py` directly.

**Adding new filter kind** — (1) add literal to `FilterKind` in `rule_compiler.py`; (2) update `_validate_filter_invariants` with required-field rule; (3) add matcher function in `rule_engine.py`; (4) extend `_FILTER_GUIDE` so LLM knows about it; (5) add test in `test_rule_compiler.py` + `test_rule_engine.py`.

**Router path gotcha** — FastAPI routers mounted at `/api/chats` + `/api/rules` use `@router.get("")` / `@router.post("")`, **not** `"/"`. Trailing-slash form triggers `307` redirect that CORS preflight (`OPTIONS`) can't follow, browser console fills with errors. Don't "fix" empty string back to `"/"`.

### Tool-call presentation (frontend)

`MessageList.tsx` renders single assistant turn's tool calls as either:
- **1 call** → standalone `<ToolCallCard>` (collapsible card with args + result)
- **2+ calls** → one `<ToolGroupCard>` summarizing `{integration} · N tools` with status rollup; expand to reveal each child `ToolCallCard` independently expandable.

Don't revert to flat per-call list — noise from chained tool calls (e.g., 4 consecutive `slack__fetch_conversation_history` calls in one turn) was explicit reason for group card.

## Smithery Connect — how MCP wiring works

Smithery = **hosted MCP gateway**: holds third-party OAuth tokens, runs MCP servers, exposes uniform HTTP JSON-RPC endpoint per user-connection. Backend never sees Jira/GitHub/Slack/Notion token.

**Auth & transport**
- Base URL: `https://api.smithery.ai` (configurable via `SMITHERY_API_BASE`).
- Every request sends `Authorization: Bearer $SMITHERY_API_KEY`.
- Transport = **Streamable HTTP**, not HTTP+SSE. Clients do **not** send `mcp-session-id` — Smithery manages sessions server-side. `backend/smithery_client.py` does plain JSON-RPC POSTs, reads back either JSON or `event-stream` body.

**Connection lifecycle** (one connection per user per integration — for this single-user app, one per integration)
- `PUT  /connect/{namespace}/{connectionId}` — create/update. Body includes target `mcpUrl` from `integrations.json`, optional `metadata` (tag with `userId` for multi-tenant; not used here).
- `GET  /connect/{namespace}/{connectionId}` — returns `state: connected | auth_required | input_required | error` plus `setupUrl` when auth needed.
- `DELETE /connect/{namespace}/{connectionId}` — revokes stored OAuth tokens.

**MCP endpoint** — `POST /connect/{namespace}/{connectionId}/mcp` with JSON-RPC 2.0 bodies: `initialize`, `notifications/initialized`, `tools/list`, `tools/call`. Status codes to know: **401** (bad API key), **404** (no such connection), **409** (`input_required` — OAuth expired, re-run setup flow).

**OAuth flow** — backend calls `upsert_connection`, gets `state=auth_required` with `setupUrl`. Frontend (`components/Integrations.tsx`) opens that URL in popup, user authorizes at upstream service, Smithery stores encrypted refresh token. Frontend polls `POST /api/integrations/{name}/refresh` every 2s until state flips to `connected`. Tokens auto-refresh; on failure state returns to `auth_required` + popup flow re-triggered.

**No first-party Python SDK.** PyPI `smithery` package is for *building* Smithery-deployable FastMCP servers, not connecting to them. Stick with existing `httpx.AsyncClient` wrapper in `backend/smithery_client.py`.

**Docs to consult when changing this layer:**
- Connect overview — https://smithery.ai/docs/use/connect
- MCP endpoint reference — https://smithery.ai/docs/api-reference/connectmcp/mcp-endpoint.md
- Create/update connection — https://smithery.ai/docs/api-reference/connect/create-or-update-connection.md
- Token scoping (namespaces, service tokens) — https://smithery.ai/docs/use/token-scoping.md
- MCP authorization deep-dive — https://smithery.ai/blog/mcp-auth

## Conventions

- Backend: `snake_case`, type hints on every signature, Pydantic models for I/O, async throughout (no sync DB calls).
- Frontend: `camelCase`, Tailwind utility classes (no CSS modules). Tailwind v4 configured through `@tailwindcss/vite` — **no** `tailwind.config.*` or PostCSS config; theme tokens live in `@theme` blocks inside `src/index.css`.
- SQLite file at `backend/data/pm.db`; `data/*.db*` gitignored.
- Don't add `tailwind.config.js`, don't introduce Axios (stick with `fetch`/`httpx`), don't persist Jira/GitHub/Slack/Notion tokens locally — Smithery owns those credentials by design.

### Glassmorphism design system (frontend/src/index.css)

UI layered over animated **aurora background** (three radial-gradient blobs on `body::before`, drifting via 38s `@keyframes` animation gated by `@media (prefers-reduced-motion: no-preference)`). Body bg = `#07070d`. Glass tokens (`--glass-bg`, `--glass-border`, `--glass-blur`, etc.) declared in top-level `@theme` block.

**Use these utility classes — don't reinvent with raw `bg-neutral-*` panels:**

| class | when to use |
|---|---|
| `.glass-panel` | default frosted card / sidebar / message bubbles |
| `.glass-elevated` | modals (`ToolApproval`), popovers (`Sidebar` chat-row menu) — stronger blur + shadow |
| `.glass-input` | inputs, textareas, secondary buttons; own focus ring |
| `.glass-row` | list rows with hover affordance; supports `data-active='true'` for active state |
| `.glass-backdrop` | modal backdrop (blurs whatever behind dialog) |
| `.glass-pre` | tinted code/`<pre>` blocks sitting *on top of* glass panel |

**Layout rules:**
- Chat surface = centered floating **island** (`max-w-4xl` glass-panel inside padded main column) — not edge-to-edge. `Composer` rendered **inside** that island with soft top divider, not as full-width bottom bar.
- Custom thin translucent scrollbars applied globally in `index.css` (`*::-webkit-scrollbar` rules + `scrollbar-width: thin`); leave element-level overrides alone.
- Gradient action buttons (Save, Send, Approve) keep semantic color but add `shadow-lg shadow-{color}/25 ring-1 ring-{color}/20` to harmonize with glass language.

Don't hard-code `bg-[#0b0b10]` or solid `bg-neutral-800` panels — break aurora layering.

**Native `<select>` options** — browsers render open `<option>` list with OS-native styling that ignores parent Tailwind classes. Every `<option>` inside glass `<select>` must set `className="bg-neutral-900 text-neutral-100"` explicitly, or list renders white-on-white on Windows themes.

### Frontend state split — `store.ts` (not `stores/chatStore.ts`)

Chat/approval/settings store lives at `frontend/src/store.ts`. Earlier plan referenced `stores/chatStore.ts` — that path does not exist. Use `store.ts`.

"Rules activity" chat recognised by `kind === "system_rules_activity"` on `Chat` type. `Sidebar` pins it to top with distinct icon; `userLastSeenActivityAt` (persisted to `localStorage`) drives unread badge via `markActivitySeen()`.

## Backend schema migrations

`db/session.py::_apply_schema_patches(conn)` runs after `SQLModel.metadata.create_all` on every boot, uses `PRAGMA table_info(...)` + `ALTER TABLE ... ADD COLUMN` to retrofit columns added after DB file first created. Idempotent + SQLite-friendly. Add new entry here for any non-table-creating schema change (e.g., new column on existing table). Creating new tables needs no patch — `create_all` handles that.