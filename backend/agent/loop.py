from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from agent.policies import requires_approval
from db.models import Conversation, Message


# Cap replayed history length. Guards against unbounded anchoring on a
# poisoned early turn in very long conversations; the tail is almost
# always what the LLM needs anyway.
MAX_HISTORY_MESSAGES = 200


SystemPromptBuilder = Callable[[], Awaitable[str]]


class _McpLike(Protocol):
    def list_all_tools(self) -> list[dict[str, Any]]: ...

    async def call_tool(self, name: str, args: dict[str, Any]) -> Any: ...


class _LLMLike(Protocol):
    def chat_stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]: ...


@dataclass
class PendingApproval:
    tool_call_id: str
    event: asyncio.Event = field(default_factory=asyncio.Event)
    approved: bool = False


def _extract_result_fields(result: Any) -> tuple[str, bool]:
    """Normalize an MCP call_tool result into (text, is_error).

    Accepts either an object with .content/.is_error attributes or a dict
    with "content"/"is_error" keys.
    """
    if isinstance(result, dict):
        is_error = bool(result.get("is_error") or result.get("isError"))
        content = result.get("content")
    else:
        is_error = bool(getattr(result, "is_error", False) or getattr(result, "isError", False))
        content = getattr(result, "content", None)
        if content is None:
            content = result

    if isinstance(content, str):
        return content, is_error
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            # mcp content items often have .text or {"type": "text", "text": "..."}
            text = getattr(item, "text", None)
            if text is None and isinstance(item, dict):
                text = item.get("text")
            if text is None:
                try:
                    text = json.dumps(item, default=str)
                except Exception:
                    text = str(item)
            parts.append(text)
        return "\n".join(parts), is_error
    if isinstance(content, dict):
        try:
            return json.dumps(content, default=str), is_error
        except Exception:
            return str(content), is_error
    return str(content), is_error


def _message_to_openai_dict(msg: Message) -> dict[str, Any]:
    """Convert a persisted Message row into an OpenAI chat-format dict."""
    base: dict[str, Any] = {"role": msg.role}
    if msg.content is not None:
        base["content"] = msg.content
    else:
        # OpenAI expects content key even if None for tool_calls-only assistant turns
        if msg.role == "assistant":
            base["content"] = None
        elif msg.role == "tool":
            base["content"] = ""
    if msg.tool_calls:
        try:
            base["tool_calls"] = json.loads(msg.tool_calls)
        except Exception:
            base["tool_calls"] = []
    if msg.role == "tool":
        if msg.tool_call_id:
            base["tool_call_id"] = msg.tool_call_id
        if msg.name:
            base["name"] = msg.name
    return base


class AgentSession:
    def __init__(
        self,
        chat_id: int,
        llm: _LLMLike,
        mcp: _McpLike,
        settings: Any,
        auto_approve: set[str],
        db_factory: async_sessionmaker[AsyncSession],
        default_model: str | None = None,
        system_prompt: str | None = None,
        system_prompt_builder: SystemPromptBuilder | None = None,
        approval_timeout: float | None = None,
        yolo_mode: bool = False,
    ) -> None:
        self.chat_id = chat_id
        self.llm = llm
        self.mcp = mcp
        self.settings = settings
        self.auto_approve = set(auto_approve or ())
        self.yolo_mode = bool(yolo_mode)
        self.db_factory = db_factory
        self.default_model = default_model or getattr(settings, "openrouter_default_model", None)
        # Builder wins when both are supplied so callers can pass a
        # callable that rebuilds the CAPABILITIES table per turn. When
        # only a static string is supplied we wrap it so the rest of the
        # class always sees a coroutine.
        if system_prompt_builder is not None:
            self._system_prompt_builder: SystemPromptBuilder = system_prompt_builder
        elif system_prompt is not None:
            _frozen = system_prompt

            async def _static_builder() -> str:
                return _frozen

            self._system_prompt_builder = _static_builder
        else:
            async def _empty_builder() -> str:
                return ""

            self._system_prompt_builder = _empty_builder
        # Cache the last-built prompt so tests and debug tooling can
        # introspect what the LLM saw on the most recent turn.
        self.system_prompt: str | None = system_prompt
        self.approval_timeout = approval_timeout
        self.pending: dict[str, PendingApproval] = {}
        self._event_counter = 0

    # ---------- public: approval gate ----------
    def approve(self, tool_call_id: str, approved: bool) -> bool:
        pending = self.pending.get(tool_call_id)
        if pending is None:
            return False
        pending.approved = approved
        pending.event.set()
        return True

    def cancel_pending(self) -> None:
        for pending in list(self.pending.values()):
            if not pending.event.is_set():
                pending.approved = False
                pending.event.set()
        self.pending.clear()

    # ---------- internal helpers ----------
    def _next_event_id(self) -> int:
        self._event_counter += 1
        return self._event_counter

    async def _load_history(self) -> list[dict[str, Any]]:
        # Rebuild system prompt each turn — the CAPABILITIES table depends
        # on live mcp.list_integrations(), and an integration flipping
        # from auth_required to connected mid-session must not leave the
        # LLM anchored on a stale snapshot. When nothing changed the
        # prompt bytes stay identical, so the OpenRouter/Claude prefix
        # cache still hits.
        prompt_text = await self._system_prompt_builder()
        self.system_prompt = prompt_text or None

        cutoff: datetime | None = None
        async with self.db_factory() as session:
            convo = await session.get(Conversation, self.chat_id)
            if convo is not None:
                cutoff = getattr(convo, "history_cutoff_at", None)

            stmt = select(Message).where(Message.conversation_id == self.chat_id)
            if cutoff is not None:
                stmt = stmt.where(Message.created_at > cutoff)
            stmt = stmt.order_by(Message.created_at, Message.id)
            rows = list((await session.execute(stmt)).scalars().all())

        # Belt-and-braces: cap the replayed window so a very long
        # conversation can't carry a single poisoned early turn forever.
        if len(rows) > MAX_HISTORY_MESSAGES:
            rows = rows[-MAX_HISTORY_MESSAGES:]

        messages: list[dict[str, Any]] = []
        if prompt_text:
            # Content-block form lets us attach an OpenRouter `cache_control`
            # breakpoint so Claude writes the prompt to its ephemeral cache
            # and Gemini's implicit cache is reinforced. Extra fields are
            # passed through to OpenRouter; providers that don't understand
            # `cache_control` simply ignore it.
            messages.append(
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "text",
                            "text": prompt_text,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                }
            )
        messages.extend(_message_to_openai_dict(m) for m in rows)
        return messages

    async def reset_history(self, cutoff: datetime | None = None) -> datetime:
        """Mark prior messages as invisible to the LLM going forward.

        Non-destructive: rows stay in the DB (audit + future UI
        reference), but ``_load_history`` filters them out on subsequent
        turns. Returns the cutoff that was written so callers can echo
        it back to the user if useful.
        """
        effective = cutoff or datetime.now(UTC)
        async with self.db_factory() as session:
            convo = await session.get(Conversation, self.chat_id)
            if convo is None:
                return effective
            convo.history_cutoff_at = effective
            session.add(convo)
            await session.commit()
        # Clear pending approvals so a stale gate doesn't haunt the
        # freshly-reset session.
        self.cancel_pending()
        return effective

    async def _persist_user_message(self, text: str) -> int:
        async with self.db_factory() as session:
            msg = Message(conversation_id=self.chat_id, role="user", content=text)
            session.add(msg)
            await self._touch_conversation(session)
            await session.commit()
            await session.refresh(msg)
            return msg.id  # type: ignore[return-value]

    async def _persist_assistant_message(
        self, content: str | None, tool_calls: list[dict[str, Any]] | None
    ) -> int:
        async with self.db_factory() as session:
            msg = Message(
                conversation_id=self.chat_id,
                role="assistant",
                content=content if content else None,
                tool_calls=json.dumps(tool_calls) if tool_calls else None,
            )
            session.add(msg)
            await self._touch_conversation(session)
            await session.commit()
            await session.refresh(msg)
            return msg.id  # type: ignore[return-value]

    async def _persist_tool_message(
        self, tool_call_id: str, name: str, content: str
    ) -> int:
        async with self.db_factory() as session:
            msg = Message(
                conversation_id=self.chat_id,
                role="tool",
                content=content,
                tool_call_id=tool_call_id,
                name=name,
            )
            session.add(msg)
            await self._touch_conversation(session)
            await session.commit()
            await session.refresh(msg)
            return msg.id  # type: ignore[return-value]

    async def _touch_conversation(self, session: AsyncSession) -> None:
        from datetime import UTC, datetime

        convo = await session.get(Conversation, self.chat_id)
        if convo is not None:
            convo.updated_at = datetime.now(UTC)
            session.add(convo)

    # ---------- public: run a user turn ----------
    async def run_turn(self, user_text: str) -> AsyncIterator[dict[str, Any]]:
        try:
            await self._persist_user_message(user_text)
        except Exception as exc:
            yield {
                "type": "error",
                "event_id": self._next_event_id(),
                "error": f"failed to persist user message: {exc}",
            }
            return

        # Load conversation state ONCE per user turn; the iteration loop
        # below extends `messages` in-process rather than re-querying the
        # DB or rebuilding the system prompt every iteration. Re-loading
        # mid-turn would also drop any cache_control breakpoint Claude
        # had pinned to the prefix.
        try:
            messages = await self._load_history()
        except Exception as exc:
            yield {
                "type": "error",
                "event_id": self._next_event_id(),
                "error": f"failed to load history: {exc}",
            }
            return
        tools = self.mcp.list_all_tools() or None

        # bounded loop against runaway tool-call chains
        max_iterations = 20
        for _ in range(max_iterations):
            assembly_text = ""
            # tool-call fragments keyed by index during streaming (OpenAI pattern)
            tc_buffer: dict[int, dict[str, Any]] = {}
            final_message: Any | None = None

            try:
                stream = self.llm.chat_stream(
                    model=self.default_model,
                    messages=messages,
                    tools=tools,
                    parallel_tool_calls=True,
                    extra_body={"provider": {"sort": "latency"}},
                )
                async for chunk in stream:
                    choices = getattr(chunk, "choices", None) or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = getattr(choice, "delta", None)
                    if delta is None:
                        continue

                    content = getattr(delta, "content", None)
                    if content:
                        assembly_text += content
                        yield {
                            "type": "token",
                            "event_id": self._next_event_id(),
                            "delta": content,
                        }

                    tool_calls = getattr(delta, "tool_calls", None)
                    if tool_calls:
                        for tc in tool_calls:
                            idx = getattr(tc, "index", 0) or 0
                            slot = tc_buffer.setdefault(
                                idx,
                                {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                            )
                            tc_id = getattr(tc, "id", None)
                            if tc_id:
                                slot["id"] = tc_id
                            fn = getattr(tc, "function", None)
                            if fn is not None:
                                name = getattr(fn, "name", None)
                                if name:
                                    slot["function"]["name"] = name
                                args_piece = getattr(fn, "arguments", None)
                                if args_piece:
                                    slot["function"]["arguments"] += args_piece

                    if getattr(choice, "finish_reason", None):
                        final_message = choice
            except Exception as exc:
                yield {
                    "type": "error",
                    "event_id": self._next_event_id(),
                    "error": f"llm stream failed: {exc}",
                }
                return

            assembled_tool_calls: list[dict[str, Any]] = []
            for idx in sorted(tc_buffer.keys()):
                entry = tc_buffer[idx]
                if not entry.get("id") or not entry["function"].get("name"):
                    continue
                assembled_tool_calls.append(
                    {
                        "id": entry["id"],
                        "type": entry.get("type", "function"),
                        "function": {
                            "name": entry["function"]["name"],
                            "arguments": entry["function"]["arguments"] or "{}",
                        },
                    }
                )

            try:
                assistant_msg_id = await self._persist_assistant_message(
                    assembly_text or None,
                    assembled_tool_calls or None,
                )
            except Exception as exc:
                yield {
                    "type": "error",
                    "event_id": self._next_event_id(),
                    "error": f"failed to persist assistant message: {exc}",
                }
                return

            # Mirror the assistant turn into the in-memory transcript so
            # the next iteration sees it without a DB round-trip.
            assistant_dict: dict[str, Any] = {
                "role": "assistant",
                "content": assembly_text if assembly_text else None,
            }
            if assembled_tool_calls:
                assistant_dict["tool_calls"] = assembled_tool_calls
            messages.append(assistant_dict)

            if not assembled_tool_calls:
                yield {
                    "type": "done",
                    "event_id": self._next_event_id(),
                    "message_id": assistant_msg_id,
                }
                return

            # Partition: non-approval calls run in parallel, approval calls
            # run sequentially (each blocks on the UI approval gate).
            auto_bucket: list[dict[str, Any]] = []
            approval_bucket: list[dict[str, Any]] = []
            for tc in assembled_tool_calls:
                tc_name = tc["function"]["name"]
                if requires_approval(tc_name, self.auto_approve, yolo=self.yolo_mode):
                    approval_bucket.append(tc)
                else:
                    auto_bucket.append(tc)

            # Pre-parse args for every auto call and emit tool_call_start so
            # the UI can render an in-flight indicator immediately.
            auto_parsed: list[tuple[dict[str, Any], dict[str, Any]]] = []
            for tc in auto_bucket:
                tc_id = tc["id"]
                tc_name = tc["function"]["name"]
                raw_args = tc["function"]["arguments"] or "{}"
                try:
                    tc_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    if not isinstance(tc_args, dict):
                        tc_args = {}
                except Exception:
                    tc_args = {}
                auto_parsed.append((tc, tc_args))
                yield {
                    "type": "tool_call_start",
                    "event_id": self._next_event_id(),
                    "tool_call_id": tc_id,
                    "tool_name": tc_name,
                    "arguments": tc_args,
                }

            async def _execute_auto(
                tc: dict[str, Any], tc_args: dict[str, Any]
            ) -> dict[str, Any]:
                tc_id = tc["id"]
                tc_name = tc["function"]["name"]
                try:
                    raw_result = await self.mcp.call_tool(tc_name, tc_args)
                    result_text, is_error = _extract_result_fields(raw_result)
                except Exception as exc:
                    result_text = f"Tool execution failed: {exc}"
                    is_error = True
                await self._persist_tool_message(tc_id, tc_name, result_text)
                return {
                    "tool_call_id": tc_id,
                    "tool_name": tc_name,
                    "result": result_text,
                    "is_error": is_error,
                }

            auto_tasks = [
                asyncio.create_task(_execute_auto(tc, args))
                for tc, args in auto_parsed
            ]
            for coro in asyncio.as_completed(auto_tasks):
                out = await coro
                # Mirror the tool result into the in-memory transcript
                # so the next iteration's LLM sees it without re-reading
                # the DB.
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": out["tool_call_id"],
                        "name": out["tool_name"],
                        "content": out["result"],
                    }
                )
                yield {
                    "type": "tool_call_result",
                    "event_id": self._next_event_id(),
                    "tool_call_id": out["tool_call_id"],
                    "tool_name": out["tool_name"],
                    "result": out["result"],
                    "is_error": out["is_error"],
                }

            # Approval-required calls: sequential gate (unchanged semantics).
            for tc in approval_bucket:
                tc_id = tc["id"]
                tc_name = tc["function"]["name"]
                raw_args = tc["function"]["arguments"] or "{}"
                try:
                    tc_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    if not isinstance(tc_args, dict):
                        tc_args = {}
                except Exception:
                    tc_args = {}

                pending = PendingApproval(tool_call_id=tc_id)
                self.pending[tc_id] = pending
                yield {
                    "type": "tool_call_request",
                    "event_id": self._next_event_id(),
                    "tool_call_id": tc_id,
                    "tool_name": tc_name,
                    "arguments": tc_args,
                }
                try:
                    if self.approval_timeout is not None:
                        await asyncio.wait_for(pending.event.wait(), self.approval_timeout)
                    else:
                        await pending.event.wait()
                except asyncio.TimeoutError:
                    pending.approved = False
                finally:
                    approved = pending.approved
                    self.pending.pop(tc_id, None)

                if not approved:
                    result_text = "User rejected this action."
                    is_error = True
                    await self._persist_tool_message(tc_id, tc_name, result_text)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "name": tc_name,
                            "content": result_text,
                        }
                    )
                    yield {
                        "type": "tool_call_result",
                        "event_id": self._next_event_id(),
                        "tool_call_id": tc_id,
                        "tool_name": tc_name,
                        "result": result_text,
                        "is_error": is_error,
                    }
                    continue

                try:
                    raw_result = await self.mcp.call_tool(tc_name, tc_args)
                    result_text, is_error = _extract_result_fields(raw_result)
                except Exception as exc:
                    result_text = f"Tool execution failed: {exc}"
                    is_error = True

                await self._persist_tool_message(tc_id, tc_name, result_text)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "name": tc_name,
                        "content": result_text,
                    }
                )
                yield {
                    "type": "tool_call_result",
                    "event_id": self._next_event_id(),
                    "tool_call_id": tc_id,
                    "tool_name": tc_name,
                    "result": result_text,
                    "is_error": is_error,
                }

            # loop again — LLM now sees the new tool results

        yield {
            "type": "error",
            "event_id": self._next_event_id(),
            "error": "agent exceeded max tool-call iterations",
        }
