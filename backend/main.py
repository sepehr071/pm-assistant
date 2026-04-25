from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import chats, integrations, rules, stream, telegram as telegram_api, tools
from api import settings as settings_router
from config import get_settings
from db.session import async_session_factory, init_db
from integrations.telegram import is_configured as _tg_is_configured, start_polling as _tg_start_polling
from llm.openrouter import OpenRouterClient
from mcp_manager import MCPManager


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    await init_db()

    llm = OpenRouterClient(settings)
    mcp = MCPManager(
        settings.integrations_config_path,
        smithery_api_key=settings.smithery_api_key,
        smithery_namespace=settings.smithery_namespace,
        smithery_api_base=settings.smithery_api_base,
    )
    await mcp.start()

    app.state.settings = settings
    app.state.llm = llm
    app.state.mcp = mcp
    app.state.scheduler = None

    # Best-effort scheduler boot. Rule execution is a background capability;
    # if Agent B's module is missing or fails to initialize, the rest of the
    # API (chats, integrations, settings) must keep serving.
    try:
        from services.rule_scheduler import RuleScheduler

        scheduler = RuleScheduler(
            mcp=mcp,
            llm=llm,
            settings=settings,
            db_factory=async_session_factory,
            default_model=settings.openrouter_default_model,
        )
        await scheduler.start()
        app.state.scheduler = scheduler
    except Exception:
        # Scheduler failure must not prevent the app from starting. Rules
        # endpoints still respond; firings simply won't run until scheduler
        # recovers on next restart.
        app.state.scheduler = None

    # Chat-driven rule creation: inject the `rules__create_rule` builtin so
    # the LLM can persist a Rule from a conversation. Safe even if scheduler
    # is None — the rule still lands in the DB and is picked up on restart.
    try:
        from services.rule_tool import register as register_rule_tool

        register_rule_tool(
            mcp,
            llm=llm,
            db_factory=async_session_factory,
            scheduler=app.state.scheduler,
            default_model=settings.openrouter_default_model,
        )
    except Exception:
        pass

    # Telegram gateway: polling starts only when bot token env is set AND
    # user has toggled it on in settings. Any failure here is swallowed —
    # API boot must not depend on Telegram being reachable.
    app.state.telegram = None
    try:
        from api.settings import read_settings_values

        _tg_values = await read_settings_values()
        _tg_token = settings.telegram_bot_token
        if _tg_is_configured(_tg_token) and bool(_tg_values.get("telegram_enabled")):
            app.state.telegram = await _tg_start_polling(_tg_token, app_state=app.state)
    except Exception:
        app.state.telegram = None

    try:
        yield
    finally:
        _tg_handle = getattr(app.state, "telegram", None)
        if _tg_handle is not None:
            try:
                await _tg_handle.stop()
            except Exception:
                pass
        scheduler = getattr(app.state, "scheduler", None)
        if scheduler is not None:
            try:
                await scheduler.stop()
            except Exception:
                pass
        try:
            await mcp.stop()
        finally:
            aclose = getattr(llm, "aclose", None)
            if callable(aclose):
                try:
                    await aclose()
                except Exception:
                    pass
            try:
                from api.stream import _reset_sessions

                _reset_sessions()
            except Exception:
                pass


app = FastAPI(title="PM Assistant", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chats.router)
app.include_router(stream.router)
app.include_router(tools.router)
app.include_router(settings_router.router)
app.include_router(integrations.router)
app.include_router(rules.router)
app.include_router(telegram_api.settings_router)
app.include_router(telegram_api.status_router)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
