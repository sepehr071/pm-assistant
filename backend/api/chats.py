from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import delete, desc, select

from db.models import Conversation, Message
from db.session import async_session_factory

router = APIRouter(prefix="/api/chats", tags=["chats"])


class CreateChatBody(BaseModel):
    title: str | None = None


class PatchChatBody(BaseModel):
    title: str | None = None


class ConversationOut(BaseModel):
    id: int
    title: str
    kind: str
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    id: int
    conversation_id: int
    role: str
    content: str | None = None
    tool_calls: str | None = None
    tool_call_id: str | None = None
    name: str | None = None
    created_at: datetime


def _convo_out(c: Conversation) -> ConversationOut:
    return ConversationOut(
        id=c.id,  # type: ignore[arg-type]
        title=c.title,
        kind=c.kind,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


def _msg_out(m: Message) -> MessageOut:
    return MessageOut(
        id=m.id,  # type: ignore[arg-type]
        conversation_id=m.conversation_id,
        role=m.role,
        content=m.content,
        tool_calls=m.tool_calls,
        tool_call_id=m.tool_call_id,
        name=m.name,
        created_at=m.created_at,
    )


@router.get("", response_model=list[ConversationOut])
async def list_chats() -> list[ConversationOut]:
    async with async_session_factory() as session:
        stmt = select(Conversation).order_by(desc(Conversation.updated_at))
        rows = (await session.execute(stmt)).scalars().all()
    return [_convo_out(c) for c in rows]


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
async def create_chat(body: CreateChatBody) -> ConversationOut:
    title = (body.title or "New chat").strip() or "New chat"
    now = datetime.now(UTC)
    async with async_session_factory() as session:
        convo = Conversation(title=title, created_at=now, updated_at=now)
        session.add(convo)
        await session.commit()
        await session.refresh(convo)
    return _convo_out(convo)


@router.get("/{chat_id}/messages", response_model=list[MessageOut])
async def list_messages(chat_id: int) -> list[MessageOut]:
    async with async_session_factory() as session:
        convo = await session.get(Conversation, chat_id)
        if convo is None:
            raise HTTPException(status_code=404, detail="chat not found")
        stmt = (
            select(Message)
            .where(Message.conversation_id == chat_id)
            .order_by(Message.created_at, Message.id)
        )
        rows = (await session.execute(stmt)).scalars().all()
    return [_msg_out(m) for m in rows]


@router.patch("/{chat_id}", response_model=ConversationOut)
async def patch_chat(chat_id: int, body: PatchChatBody) -> ConversationOut:
    async with async_session_factory() as session:
        convo = await session.get(Conversation, chat_id)
        if convo is None:
            raise HTTPException(status_code=404, detail="chat not found")
        if body.title is not None:
            title = body.title.strip()
            if title:
                convo.title = title
        convo.updated_at = datetime.now(UTC)
        session.add(convo)
        await session.commit()
        await session.refresh(convo)
    return _convo_out(convo)


@router.delete("/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat(chat_id: int) -> None:
    async with async_session_factory() as session:
        convo = await session.get(Conversation, chat_id)
        if convo is None:
            raise HTTPException(status_code=404, detail="chat not found")
        await session.execute(delete(Message).where(Message.conversation_id == chat_id))
        await session.delete(convo)
        await session.commit()
    return None
