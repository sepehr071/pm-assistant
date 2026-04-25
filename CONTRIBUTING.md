# Contributing to PM Assistant

Thanks for considering a contribution. This is a small, opinionated codebase — keep changes focused and the bar high.

## Before you start

- For non-trivial work, **open an issue first** and wait for a maintainer ack. Saves throwaway PRs.
- Read [`CLAUDE.md`](CLAUDE.md) — it documents the architecture, conventions, and the gotchas that aren't obvious from grepping.

## Workflow

1. Fork the repo and branch from `main`. Use `feat/...`, `fix/...`, `refactor/...`, `docs/...`, `test/...` prefixes.
2. Run the project locally and reproduce the issue or build the feature.
3. **Add tests.** Backend changes need `pytest` coverage; frontend changes need `vitest` where it makes sense. The bar is "would this catch the regression next time?".
4. Run all checks before pushing:
   ```bash
   cd backend  && uv run pytest
   cd frontend && pnpm lint && pnpm test --run && pnpm build
   ```
5. Commit using [Conventional Commits](https://www.conventionalcommits.org/): `<type>(<scope>): <description>`.
6. Open a PR against `main`, link the issue, fill in the template.

## House rules

- **Backend:** `snake_case`, type-hinted on every signature, async throughout. No sync DB calls. Pydantic models for I/O.
- **Frontend:** `camelCase`, Tailwind utility classes only — no CSS modules, no `tailwind.config.*`. Theme tokens live in `@theme` blocks inside `src/index.css`.
- **Package managers:** `uv` for Python, `pnpm` for Node. Do not introduce `pip` or `npm`.
- **HTTP:** `fetch` (frontend) and `httpx` (backend) only. No Axios.
- **Glassmorphism:** use the `.glass-*` utility classes (`glass-panel`, `glass-elevated`, `glass-input`, `glass-row`, `glass-backdrop`, `glass-pre`). Don't hard-code solid panel backgrounds.
- **SSE events:** new event type? Add handlers on **both** sides — the frontend silently drops unknown events.
- **Settings mutations** drop the in-memory agent session by design. Don't try to keep one alive across `PATCH /api/settings`.

## Adding integrations

`backend/integrations.json` is fully data-driven. Add an entry with `label` + `mcpUrl` (the canonical URL shown on that server's [Smithery](https://smithery.ai) page). Restart the backend; the row appears in **Settings → Integrations** automatically. No backend code change required.

The agent's tool gate is server-agnostic: read/write classification is verb-based (see `backend/agent/policies.py`), so new integrations inherit the policy with zero config.

## Adding a new rule filter kind

See `CLAUDE.md` → **Adding new filter kind** for the five-step recipe (literal in `FilterKind` → invariant validator → matcher → LLM guide → tests).

## Reporting security issues

Do **not** open a public issue for a security vulnerability. Email the maintainer first.
