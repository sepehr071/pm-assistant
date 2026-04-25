from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel

from config import get_settings
from db import models  # noqa: F401  (register table metadata)

_settings = get_settings()

engine: AsyncEngine = create_async_engine(
    _settings.database_url,
    echo=False,
    future=True,
)

async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


def _ensure_sqlite_dir(database_url: str) -> None:
    if not database_url.startswith("sqlite"):
        return
    # Extract path after the last ':///' segment
    _, _, path_part = database_url.partition(":///")
    if not path_part:
        return
    db_path = Path(path_part)
    if db_path.parent and not db_path.parent.exists():
        db_path.parent.mkdir(parents=True, exist_ok=True)


async def _apply_schema_patches(conn: AsyncConnection) -> None:
    """Add columns introduced after a DB file was first created.

    SQLModel.metadata.create_all creates missing tables but never alters
    existing ones. Keep these patches idempotent and SQLite-friendly.
    """
    conv_rows = (await conn.execute(text("PRAGMA table_info(conversation)"))).all()
    conv_cols = {row[1] for row in conv_rows}
    if conv_rows and "kind" not in conv_cols:
        await conn.execute(
            text("ALTER TABLE conversation ADD COLUMN kind VARCHAR NOT NULL DEFAULT 'user'")
        )
    if conv_rows and "history_cutoff_at" not in conv_cols:
        await conn.execute(
            text("ALTER TABLE conversation ADD COLUMN history_cutoff_at DATETIME NULL")
        )

    rule_rows = (await conn.execute(text("PRAGMA table_info(rule)"))).all()
    rule_cols = {row[1] for row in rule_rows}
    if rule_rows and "activity_conversation_id" not in rule_cols:
        await conn.execute(
            text("ALTER TABLE rule ADD COLUMN activity_conversation_id INTEGER NULL")
        )

    firing_rows = (await conn.execute(text("PRAGMA table_info(rulefiring)"))).all()
    firing_cols = {row[1] for row in firing_rows}
    if firing_rows and "conversation_id" not in firing_cols:
        await conn.execute(
            text("ALTER TABLE rulefiring ADD COLUMN conversation_id INTEGER NULL")
        )
        # One-shot backfill for existing firings: point them at the legacy
        # singleton activity chat (if one exists).
        await conn.execute(
            text(
                "UPDATE rulefiring SET conversation_id = "
                "(SELECT id FROM conversation WHERE kind='system_rules_activity' LIMIT 1) "
                "WHERE conversation_id IS NULL"
            )
        )
    if firing_rows and "duration_ms" not in firing_cols:
        await conn.execute(
            text("ALTER TABLE rulefiring ADD COLUMN duration_ms INTEGER NULL")
        )
    if firing_rows and "trigger_summary" not in firing_cols:
        await conn.execute(
            text("ALTER TABLE rulefiring ADD COLUMN trigger_summary VARCHAR NULL")
        )

    # Indexes added after initial create_all — only when the underlying
    # table exists, so unit tests that call this helper standalone don't
    # trip on the still-missing tables.
    msg_rows = (await conn.execute(text("PRAGMA table_info(message)"))).all()
    if msg_rows:
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_message_created_at ON message(created_at)")
        )
    if firing_rows:
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_rulefiring_fired_at ON rulefiring(fired_at)")
        )


async def init_db() -> None:
    _ensure_sqlite_dir(_settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        await _apply_schema_patches(conn)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session
