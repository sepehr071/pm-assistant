# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-04-25

Initial public release of **PM Assistant** — a local-first project-management
copilot that streams LLM responses from OpenRouter, executes MCP tool calls
through Smithery Connect against nine third-party services, and runs proactive
recurring rules from natural-language descriptions.

### Added

- **Streaming chat agent loop** — `AgentSession` orchestrates one turn:
  streams OpenRouter completions, assembles tool-call fragments by `index`,
  consults the policy gate per tool call, dispatches reads immediately and
  pauses on writes, feeds results back into the LLM. Bounded to 20 iterations
  per turn (`backend/agent/loop.py`).
- **Nine Smithery-hosted MCP integrations** — Jira, GitHub, Slack, Notion,
  Linear, Google Calendar, Figma, Gmail, and Confluence, declared in
  `backend/integrations.json`. Adding/removing an integration is a single-file
  edit; no per-server backend code required.
- **Per-connection OAuth via Smithery** — backend never sees upstream tokens.
  Frontend opens Smithery's `setupUrl` in a popup and polls
  `POST /api/integrations/{name}/refresh` every 2 s until state flips to
  `connected` (`backend/api/integrations.py`,
  `frontend/src/components/Integrations.tsx`).
- **Three-section system prompt** built per session — hardcoded BASE
  (identity, naming convention, approval semantics), CAPABILITIES (live
  markdown table of integrations + tool counts), and an optional EXTENSION
  rendered from the user's "Additional instructions" setting
  (`backend/agent/system_prompt.py`).
- **SSE event contract** with five typed events (`token`,
  `tool_call_request`, `tool_call_result`, `done`, `error`) consumed by
  `frontend/src/lib/stream.ts`.
- **Tool namespacing** — tools exposed to the LLM as
  `{integration}__{tool}`; `MCPManager.call_tool` splits on the double
  underscore to route to the right Smithery connection
  (`backend/mcp_manager.py`).
- **Built-in in-process tool registry** — `MCPManager.register_builtin`
  lets non-MCP tools (currently `rules__create_rule`) be exposed alongside
  Smithery tools through the same dispatch path (`backend/services/rule_tool.py`).
- **Proactive rules engine** — natural-language rules compile to a
  closed-set JSON spec (`response_format=json_object` with one retry on
  parse failure), persist to a `Rule` row, and run on an in-process
  `APScheduler` poll. Hot path is LLM-free: poll the source MCP tool, run
  a pure-python matcher, only call the LLM on a match
  (`backend/services/rule_compiler.py`, `backend/services/rule_engine.py`,
  `backend/services/rule_scheduler.py`).
- **Two ways to create a rule** — manual `RuleEditor` two-step form on
  `/rules`, or chat-driven via the built-in `rules__create_rule` tool
  triggered from any natural-language "whenever…" intent
  (`frontend/src/components/RuleEditor.tsx`,
  `backend/services/rule_tool.py`).
- **Pinned "Rules activity" system chat** — single seeded
  `Conversation.kind = "system_rules_activity"` row, distinct sidebar pin
  with unread badge driven by `userLastSeenActivityAt` in `localStorage`
  (`frontend/src/components/Sidebar.tsx`, `frontend/src/store.ts`).
- **`RuleFiring` audit log** — one row per scheduler tick with
  `status ∈ {matched, no_match, error, pending_approval, expired,
  rejected}` and an optional `message_id` link into the activity chat.
  Automatically pruned after 30 days
  (`backend/services/rule_scheduler.py::_DEFAULT_FIRING_RETENTION_DAYS = 30`).
- **Telegram bridge** — out-of-band approval channel. Pairing flow uses
  six-character codes (ambiguous chars stripped) with TTL
  (`backend/integrations/telegram/pairing.py`); approve/reject via
  `callback_query` buttons on Telegram messages, validated against bound
  `chat_id` and `from_user.id` before flipping the gate
  (`backend/integrations/telegram/bot.py`).
- **YOLO mode + per-tool auto-approve list** — global toggle bypasses the
  gate; per-tool allowlist auto-approves specific qualified names without
  disabling the gate elsewhere (`backend/api/settings.py`,
  `frontend/src/pages/Settings.tsx`).
- **`show_tool_details` setting** — collapses tool-call cards to a single
  status row by default; toggle reveals arguments and results
  (`frontend/src/components/ToolCallCard.tsx`,
  `frontend/src/components/ToolGroupCard.tsx`).
- **Tool-call presentation** — single tool call renders a standalone
  `ToolCallCard`; two or more in one assistant turn collapse into a
  `ToolGroupCard` with status rollup and per-child expansion
  (`frontend/src/components/MessageList.tsx`).
- **Glassmorphism design system** — animated aurora background (three
  drifting radial-gradient blobs, gated by
  `prefers-reduced-motion: no-preference`), glass utility classes
  (`.glass-panel`, `.glass-elevated`, `.glass-input`, `.glass-row`,
  `.glass-backdrop`, `.glass-pre`), centered chat island layout, custom
  thin translucent scrollbars (`frontend/src/index.css`).
- **Per-integration brand tiles** — bespoke gradient + ring tokens for
  each of the nine integrations rendered in Settings → Integrations
  (`frontend/src/components/brands.ts`).
- **Schema patch runner** — `db/session.py::_apply_schema_patches` retrofits
  new columns on every boot via `PRAGMA table_info` + `ALTER TABLE ADD
  COLUMN`, idempotent and SQLite-friendly.

### Changed (vs nothing — initial release baseline)

- Frontend chat surface ships as a centered floating glass island
  (`max-w-4xl` panel with embedded `Composer`) rather than an
  edge-to-edge layout.
- `Conversation.kind` defaults to `"user"`; the rules activity chat is
  the only `"system_rules_activity"` row, seeded from `main.py` lifespan.

### Security

- **Default-deny approval gate** — verb-token classifier in
  `backend/agent/policies.py`: 75 write tokens (`create`, `update`,
  `delete`, `post`, `send`, `merge`, `close`, `archive`, `transition`,
  `reset`, `revoke`, …) and 42 read tokens (`get`, `list`, `search`,
  `read`, `find`, `view`, `query`, `fetch`, `whoami`, …). A tool is
  treated as a write unless **none** of its underscore-separated
  segments hit a write token **and** at least one segment hits a read
  token. Kills prefix-only bypasses like `get_or_create_issue` and
  `read_and_post`. Server-name-agnostic — new integrations inherit the
  policy with zero config.
- **Smithery error-body redaction** — `_safe_error_excerpt` strips
  `Bearer <token>`, `setupUrl=…`, and `(authorization|api[_-]?key|token|
  access_token|refresh_token|client_secret)` substrings before any
  Smithery upstream error body lands in a persisted `last_error`,
  `RuleFiring.error`, or SSE `error` event (`backend/smithery_client.py`).
- **Telegram callback authorization** — every approve/reject callback
  validates `from.id == chat.id == bound_chat_id` before mutating the
  approval gate; mismatches receive a `Not authorized for this bot.`
  alert (`backend/integrations/telegram/bot.py`).
- **No third-party tokens stored locally** — Jira, GitHub, Slack, Notion,
  Linear, Google Calendar, Figma, Gmail, and Confluence credentials live
  exclusively in Smithery; backend only ever sees `Bearer
  $SMITHERY_API_KEY`.
- **Auto-disable on repeated failure** — a rule that errors five
  consecutive ticks is auto-disabled, with the failure count exposed
  through `GET /api/rules/health` for early warning
  (`backend/services/rule_engine.py::_AUTO_DISABLE_THRESHOLD = 5`).

### Performance

- **Per-turn agent-loop state caching** — conversation history loaded
  once per user turn; the iteration loop extends `messages` in-process
  rather than re-querying the DB or rebuilding the system prompt every
  iteration. Preserves any prefix `cache_control` breakpoint pinned by
  the model (`backend/agent/loop.py::run_turn`).
- **Rules hot path is LLM-free** — scheduler tick polls the source MCP
  tool, applies a pure-python matcher, and only spins up an
  `AgentSession` when the filter matches. `state_cursor` prevents
  re-firing on the same trigger across ticks.
- **rAF-coalesced streaming** — incoming SSE `token` deltas are batched
  inside a `requestAnimationFrame` callback so the chat list updates at
  most once per frame instead of once per token
  (`frontend/src/lib/stream.ts`).
- **Lazy-loaded routes** — Settings, Rules, and RuleActivity pages are
  code-split via `React.lazy`, kept out of the initial bundle
  (`frontend/src/App.tsx`).
- **Frontend code-splitting** — initial chunk lands at ~420 KB
  (`dist/assets/index-*.js`, ~129 KB gzip), roughly 30 % lighter than
  the equivalent monolith bundle. Settings (~25 KB), Rules (~20 KB),
  and RuleActivity (~9 KB) ship as separate async chunks.
- **Smooth pending → success tool-card transition** — `ToolCallCard`
  remains mounted across the status flip; the amber pending state
  cross-fades to emerald success rather than unmounting and remounting
  (`frontend/src/components/MessageList.tsx`,
  `frontend/src/components/ToolCallCard.tsx`).
- **APScheduler tuning** — `max_instances=1` and
  `misfire_grace_time=interval_seconds` per rule prevent overlapping
  ticks and tame catch-up storms after pause/resume
  (`backend/services/rule_scheduler.py`).

### Developer experience

- **uv + pnpm only** — `uv.lock` and `pnpm-lock.yaml` are the source of
  truth; pip and npm are explicitly off-limits.
- **Test coverage** — 175 backend tests across 14 files (`pytest` with
  `asyncio_mode = "auto"`, `respx` mocking httpx-to-Smithery) and 23
  frontend tests across 5 files (`vitest` + jsdom).
- **Settings mutations drop sessions** — `PATCH /api/settings` clears
  the in-memory `_sessions` dict so the next message rebuilds
  `AgentSession` against current `yolo_mode`, `auto_approve_tools`, and
  model choice without a restart (`backend/api/stream.py`).
- **Repo-root `.env`** — `backend/config.py` loads `../.env`, so a
  single env file serves both backend and any tooling run from the
  project root.
- **Empty-string FastAPI router paths** — `/api/chats` and `/api/rules`
  routes are registered with `@router.get("")` to avoid the 307 trailing-
  slash redirect that breaks CORS preflight (documented in
  `CLAUDE.md`).

[Unreleased]: https://example.com/compare/v0.1.0...HEAD
[0.1.0]: https://example.com/releases/tag/v0.1.0
