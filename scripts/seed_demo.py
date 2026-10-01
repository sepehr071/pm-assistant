"""Seed a throwaway SQLite DB with fictional demo data for screenshots.

Everything here is invented (Acme Corp, Alice, Bob, Sara...). No LLM or
Smithery call is made. Run from the repo root:

    cd backend
    DATABASE_URL=sqlite+aiosqlite:///./data/demo.db OPENROUTER_API_KEY=demo \
        uv run python ../scripts/seed_demo.py

Then start the backend with the same DATABASE_URL.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("OPENROUTER_API_KEY", "demo-not-a-key")

from db.models import Conversation, Message, Rule, RuleFiring, UserSetting  # noqa: E402
from db.session import async_session_factory, engine, init_db  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

NOW = datetime.now(UTC)


def ago(**kw: float) -> datetime:
    return NOW - timedelta(**kw)


def call(cid: str, name: str, args: dict) -> dict:
    return {
        "id": cid,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
    }


async def main() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
    await init_db()

    async with async_session_factory() as s:
        s.add(UserSetting(key="language", value=json.dumps("fa")))
        s.add(UserSetting(key="show_tool_details", value=json.dumps(False)))

        older = [
            ("Release notes v2.4 draft", 2),
            ("Figma handoff — onboarding", 5),
            ("جلسه‌ی برنامه‌ریزی Q4", 26),
            ("Weekly status for Acme Corp", 50),
        ]
        for title, hrs in older:
            s.add(Conversation(title=title, created_at=ago(hours=hrs), updated_at=ago(hours=hrs)))

        chat = Conversation(
            title="خلاصه‌ی اسپرینت ۱۴ و گزارش به تیم",
            created_at=ago(minutes=12),
            updated_at=ago(minutes=1),
        )
        s.add(chat)
        await s.flush()

        t = ago(minutes=12)
        step = timedelta(seconds=20)

        def msg(**kw: object) -> Message:
            nonlocal t
            t += step
            return Message(conversation_id=chat.id, created_at=t, **kw)

        s.add(msg(role="user", content="وضعیت اسپرینت ۱۴ پروژه‌ی ACME رو از Jira دربیار، PRهای باز رو هم چک کن و یه خلاصه بده."))
        s.add(msg(
            role="assistant",
            content="",
            tool_calls=json.dumps([
                call("c1", "jira__search_issues", {"jql": "project = ACME AND sprint = 14", "max_results": 50}),
                call("c2", "github__list_pull_requests", {"repo": "acme/web-app", "state": "open"}),
            ]),
        ))
        s.add(msg(role="tool", tool_call_id="c1", name="jira__search_issues",
                  content=json.dumps({"total": 23, "done": 17, "in_progress": 4, "blocked": 2})))
        s.add(msg(role="tool", tool_call_id="c2", name="github__list_pull_requests",
                  content=json.dumps({"open": 5, "awaiting_review": 3})))
        s.add(msg(role="assistant", content=(
            "## اسپرینت ۱۴ — پروژه‌ی ACME\n\n"
            "**۱۷ از ۲۳ تسک** بسته شده (۷۴٪). دو مورد بلاک است:\n\n"
            "| کلید | عنوان | مسئول | وضعیت |\n"
            "|---|---|---|---|\n"
            "| `ACME-412` | Checkout timeout on slow networks | Alice | Blocked |\n"
            "| `ACME-431` | SSO callback fails for new tenants | Bob | Blocked |\n\n"
            "**PRهای باز:** ۵ مورد؛ ۳ تا منتظر ریویو هستند، قدیمی‌ترین `#218` از دو روز پیش.\n\n"
            "پیشنهاد: `ACME-431` را در استندآپ فردا مطرح کنیم و ریویوی `#218` را به Sara بسپاریم."
        )))
        s.add(msg(role="user", content="عالیه. همین خلاصه رو توی کانال #acme-sprint اسلک پست کن."))
        s.add(msg(
            role="assistant",
            content="",
            tool_calls=json.dumps([
                call("c3", "slack__post_message", {"channel": "#acme-sprint", "text": "Sprint 14: 17/23 done, 2 blocked (ACME-412, ACME-431), 3 PRs awaiting review."}),
            ]),
        ))
        s.add(msg(role="tool", tool_call_id="c3", name="slack__post_message",
                  content=json.dumps({"ok": True, "channel": "#acme-sprint"})))
        s.add(msg(role="assistant", content="پیام در **#acme-sprint** منتشر شد — بعد از تأیید شما."))

        rules = [
            dict(
                description="وقتی Alice توی اسلک کلمه‌ی «فوری» یا urgent نوشت، بهم تلگرام بده",
                spec={
                    "source_tool": "slack__conversations_history",
                    "source_args": {"channel": "#acme-support"},
                    "filter": {"kind": "message_from_user_contains", "user": "alice", "contains": ["فوری", "urgent"]},
                    "action_prompt": "Summarize Alice's message and notify me.",
                },
                interval=3600, auto=False,
            ),
            dict(
                description="Every weekday at 9am, post a digest of blocked Jira issues to #standup",
                spec={
                    "source_tool": "jira__search_issues",
                    "source_args": {"jql": "project = ACME AND status = Blocked"},
                    "filter": {"kind": "schedule_only", "schedule": "0 9 * * 1-5"},
                    "action_prompt": "Post a short digest of blocked issues to #standup.",
                },
                interval=86400, auto=True,
            ),
            dict(
                description="When someone requests my review on acme/web-app, add it to my calendar",
                spec={
                    "source_tool": "github__list_pull_requests",
                    "source_args": {"repo": "acme/web-app"},
                    "filter": {"kind": "new_pr_review_request", "mention": "bob-demo", "repo": "acme/web-app"},
                    "action_prompt": "Create a 30-minute review block on my calendar for the PR.",
                },
                interval=7200, auto=False,
            ),
        ]
        for i, r in enumerate(rules):
            act = Conversation(title=f"Rule activity #{i + 1}", kind="rule_activity",
                               created_at=ago(days=3), updated_at=ago(hours=1))
            s.add(act)
            await s.flush()
            rule = Rule(
                description=r["description"],
                compiled_spec=json.dumps(r["spec"], ensure_ascii=False),
                interval_seconds=r["interval"],
                auto_approve=r["auto"],
                enabled=i != 2,
                last_run_at=ago(minutes=20 + i * 7),
                activity_conversation_id=act.id,
                created_at=ago(days=3),
            )
            s.add(rule)
            await s.flush()
            for k in range(18):
                if k in (1, 6, 11):
                    status, summ, diag = "matched", "Alice: «deploy فوری لازمه»" if i == 0 else "3 blocked issues → #standup", None
                elif k == 9 and i == 0:
                    status, summ, diag = "pending_approval", "Alice: urgent — customer escalation", None
                elif k == 14 and i != 1:
                    status, summ, diag = "error", "Smithery timeout", "upstream timed out after 30s"
                else:
                    status, summ, diag = "no_match", None, "No new messages matched the filter"
                s.add(RuleFiring(
                    rule_id=rule.id, conversation_id=act.id,
                    fired_at=ago(hours=k * 1.3 + i),
                    status=status, match_summary=summ, trigger_summary=summ,
                    diagnostic=diag, duration_ms=420 + (k * 137) % 900,
                ))
            s.add(Message(conversation_id=act.id, role="user", created_at=ago(hours=1, minutes=2),
                          content=f"[rule trigger] {r['spec']['action_prompt']} Context: {'Alice: «deploy فوری لازمه»' if i == 0 else '3 blocked issues'}"))
            s.add(Message(conversation_id=act.id, role="assistant", created_at=ago(hours=1),
                          content="Alice در #acme-support درخواست deploy فوری داده؛ خلاصه برای شما در تلگرام ارسال شد." if i == 0 else "Digest posted."))
        await s.commit()
    print("demo DB seeded")


if __name__ == "__main__":
    asyncio.run(main())
