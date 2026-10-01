from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from db.models import Conversation, Message, UserSetting


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def test_conversation_and_message_round_trip(session: AsyncSession) -> None:
    convo = Conversation(title="Kickoff planning")
    session.add(convo)
    await session.commit()
    await session.refresh(convo)

    assert convo.id is not None
    assert convo.title == "Kickoff planning"
    assert convo.created_at is not None
    assert convo.updated_at is not None

    user_msg = Message(
        conversation_id=convo.id,
        role="user",
        content="What's on the roadmap?",
    )
    tool_msg = Message(
        conversation_id=convo.id,
        role="tool",
        content='{"result": "ok"}',
        tool_call_id="call_abc",
        name="search_issues",
    )
    session.add(user_msg)
    session.add(tool_msg)
    await session.commit()

    result = await session.exec(
        select(Message).where(Message.conversation_id == convo.id).order_by(Message.id)
    )
    rows = result.all()
    assert len(rows) == 2
    assert rows[0].role == "user"
    assert rows[0].content == "What's on the roadmap?"
    assert rows[1].role == "tool"
    assert rows[1].tool_call_id == "call_abc"
    assert rows[1].name == "search_issues"


async def test_user_setting_primary_key(session: AsyncSession) -> None:
    setting = UserSetting(key="theme", value='"dark"')
    session.add(setting)
    await session.commit()

    got = await session.get(UserSetting, "theme")
    assert got is not None
    assert got.value == '"dark"'


async def test_schema_patches_are_idempotent() -> None:
    """Simulate an old DB with the pre-Track-A / pre-Track-B columns only,
    apply `_apply_schema_patches` twice, and confirm:
      - new columns land on first run,
      - second run is a no-op (no duplicate ADD COLUMN errors),
      - existing firings get backfilled with the legacy singleton chat id.
    """
    from db.session import _apply_schema_patches

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)

    # Create the tables with their "legacy" pre-patch shape (only the columns
    # that would have existed before Track A landed).
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE conversation ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  title VARCHAR NOT NULL,"
                "  kind VARCHAR NOT NULL DEFAULT 'user',"
                "  created_at DATETIME NOT NULL,"
                "  updated_at DATETIME NOT NULL"
                ")"
            )
        )
        await conn.execute(
            text(
                "CREATE TABLE rule ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  description VARCHAR NOT NULL,"
                "  compiled_spec VARCHAR NOT NULL,"
                "  interval_seconds INTEGER NOT NULL,"
                "  auto_approve BOOLEAN NOT NULL DEFAULT 0,"
                "  enabled BOOLEAN NOT NULL DEFAULT 1,"
                "  state_cursor VARCHAR,"
                "  last_run_at DATETIME,"
                "  last_error VARCHAR,"
                "  created_at DATETIME NOT NULL,"
                "  updated_at DATETIME NOT NULL"
                ")"
            )
        )
        await conn.execute(
            text(
                "CREATE TABLE rulefiring ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  rule_id INTEGER NOT NULL,"
                "  fired_at DATETIME NOT NULL,"
                "  status VARCHAR NOT NULL,"
                "  match_summary VARCHAR,"
                "  message_id INTEGER,"
                "  error VARCHAR"
                ")"
            )
        )
        # Legacy singleton + one firing without conversation_id set.
        await conn.execute(
            text(
                "INSERT INTO conversation (title, kind, created_at, updated_at) "
                "VALUES ('Rules activity', 'system_rules_activity', "
                "'2024-01-01 00:00:00', '2024-01-01 00:00:00')"
            )
        )
        legacy_id = (
            await conn.execute(
                text(
                    "SELECT id FROM conversation WHERE kind='system_rules_activity'"
                )
            )
        ).scalar()
        await conn.execute(
            text(
                "INSERT INTO rulefiring (rule_id, fired_at, status) "
                "VALUES (1, '2024-01-01 00:00:00', 'matched')"
            )
        )

    # First apply — adds the new columns + backfills.
    async with engine.begin() as conn:
        await _apply_schema_patches(conn)

    async with engine.begin() as conn:
        rule_cols = {
            row[1]
            for row in (await conn.execute(text("PRAGMA table_info(rule)"))).all()
        }
        firing_cols = {
            row[1]
            for row in (
                await conn.execute(text("PRAGMA table_info(rulefiring)"))
            ).all()
        }
        assert "activity_conversation_id" in rule_cols
        assert "conversation_id" in firing_cols
        assert "duration_ms" in firing_cols
        assert "trigger_summary" in firing_cols

        # Backfill applied.
        row = (
            await conn.execute(text("SELECT conversation_id FROM rulefiring"))
        ).first()
        assert row is not None
        assert row[0] == legacy_id

    # Second apply — should be a no-op, no duplicate-column error.
    async with engine.begin() as conn:
        await _apply_schema_patches(conn)

    await engine.dispose()
