"""Tests for `RuleScheduler.reload()` trigger selection.

Audit item #9: a compiled `filter.schedule` cron string must drive a
`CronTrigger`, not the rule's `interval_seconds` `IntervalTrigger`. These
tests exercise `reload()` in isolation by faking the DB factory and the
`AsyncIOScheduler` so no real scheduler thread is spun up.

We assert on the trigger objects that `reload()` registers via
`scheduler.add_job(...)`, since `str(trigger)` distinguishes both cron-vs-
interval and the interval magnitude / cron expression.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from services.rule_scheduler import RuleScheduler


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeRule:
    def __init__(
        self,
        *,
        id: int,
        compiled_spec: str,
        interval_seconds: int = 300,
        enabled: bool = True,
    ) -> None:
        self.id = id
        self.compiled_spec = compiled_spec
        self.interval_seconds = interval_seconds
        self.enabled = enabled


class _FakeJob:
    def __init__(self, id: str, trigger: Any) -> None:
        self.id = id
        self.trigger = trigger


class _FakeScheduler:
    """Minimal stand-in for AsyncIOScheduler tracking add/remove of jobs."""

    def __init__(self) -> None:
        self._jobs: dict[str, _FakeJob] = {}
        self.add_calls: list[dict[str, Any]] = []

    def add_job(self, func: Any, *, trigger: Any, id: str, **kwargs: Any) -> _FakeJob:
        self.add_calls.append({"trigger": trigger, "id": id, "kwargs": kwargs})
        job = _FakeJob(id=id, trigger=trigger)
        self._jobs[id] = job
        return job

    def get_jobs(self) -> list[_FakeJob]:
        return list(self._jobs.values())

    def get_job(self, job_id: str) -> _FakeJob | None:
        return self._jobs.get(job_id)

    def remove_job(self, job_id: str) -> None:
        self._jobs.pop(job_id, None)


class _FakeSession:
    """Async-context-manager session whose execute() returns the seeded rules."""

    def __init__(self, rules: list[_FakeRule]) -> None:
        self._rules = rules

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def execute(self, _stmt: Any) -> Any:
        rules = self._rules

        class _Result:
            def scalars(self_inner) -> Any:
                class _Scalars:
                    def all(self_s) -> list[_FakeRule]:
                        return list(rules)

                return _Scalars()

        return _Result()


def _db_factory(rules: list[_FakeRule]):
    def _factory() -> _FakeSession:
        return _FakeSession(rules)

    return _factory


def _make_scheduler(rules: list[_FakeRule]) -> RuleScheduler:
    sched = RuleScheduler(
        mcp=object(),
        llm=object(),
        settings=object(),
        db_factory=_db_factory(rules),  # type: ignore[arg-type]
        default_model="anthropic/claude-sonnet-4.6",
    )
    sched._scheduler = _FakeScheduler()  # type: ignore[assignment]
    return sched


def _spec(*, kind: str, schedule: str | None = None) -> str:
    filter_: dict[str, Any] = {"kind": kind}
    if schedule is not None:
        filter_["schedule"] = schedule
    return json.dumps(
        {
            "source_tool": "slack__conversations_history",
            "source_args": {"channel": "C1"},
            "filter": filter_,
            "action_prompt": "do the thing",
        }
    )


def _job(sched: RuleScheduler, rule_id: int) -> _FakeJob:
    return sched._scheduler.get_job(f"rule-{rule_id}")  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_schedule_only_rule_with_cron_gets_cron_trigger() -> None:
    rules = [
        _FakeRule(
            id=1,
            compiled_spec=_spec(kind="schedule_only", schedule="0 9 * * 1-5"),
            interval_seconds=300,
        )
    ]
    sched = _make_scheduler(rules)

    await sched.reload()

    job = _job(sched, 1)
    assert isinstance(job.trigger, CronTrigger)
    # The cron expression is honored, not the interval.
    expected = CronTrigger.from_crontab("0 9 * * 1-5")
    assert str(job.trigger) == str(expected)

    # cron jobs use a sane misfire grace window, single instance.
    add = sched._scheduler.add_calls[-1]  # type: ignore[attr-defined]
    assert add["kwargs"]["misfire_grace_time"] == 3600
    assert add["kwargs"]["max_instances"] == 1


async def test_rule_without_schedule_keeps_interval_trigger() -> None:
    rules = [
        _FakeRule(
            id=2,
            compiled_spec=_spec(kind="message_from_user_contains"),
            interval_seconds=120,
        )
    ]
    sched = _make_scheduler(rules)

    await sched.reload()

    job = _job(sched, 2)
    assert isinstance(job.trigger, IntervalTrigger)
    assert str(job.trigger) == str(IntervalTrigger(seconds=120))


async def test_empty_schedule_string_keeps_interval_trigger() -> None:
    # schedule present but empty/whitespace must not switch to cron.
    rules = [
        _FakeRule(
            id=3,
            compiled_spec=_spec(kind="schedule_only", schedule="   "),
            interval_seconds=300,
        )
    ]
    sched = _make_scheduler(rules)

    await sched.reload()

    job = _job(sched, 3)
    assert isinstance(job.trigger, IntervalTrigger)


async def test_invalid_cron_falls_back_to_interval_and_warns(
    caplog: Any,
) -> None:
    rules = [
        _FakeRule(
            id=4,
            compiled_spec=_spec(kind="schedule_only", schedule="not a cron"),
            interval_seconds=600,
        )
    ]
    sched = _make_scheduler(rules)

    with caplog.at_level(logging.WARNING, logger="services.rule_scheduler"):
        await sched.reload()

    job = _job(sched, 4)
    # Did not crash, did not drop the job — fell back to interval.
    assert isinstance(job.trigger, IntervalTrigger)
    assert str(job.trigger) == str(IntervalTrigger(seconds=600))
    # And it warned.
    assert any("cron" in rec.message.lower() for rec in caplog.records)
    # Rule is NOT disabled by the scheduler.
    assert rules[0].enabled is True


async def test_malformed_compiled_spec_falls_back_to_interval() -> None:
    rules = [
        _FakeRule(id=5, compiled_spec="{not json", interval_seconds=300),
    ]
    sched = _make_scheduler(rules)

    await sched.reload()

    job = _job(sched, 5)
    assert isinstance(job.trigger, IntervalTrigger)


async def test_reload_swaps_interval_to_cron_when_spec_changes() -> None:
    # First reload: plain interval rule.
    rule = _FakeRule(
        id=6,
        compiled_spec=_spec(kind="message_from_user_contains"),
        interval_seconds=300,
    )
    rules = [rule]
    sched = _make_scheduler(rules)

    await sched.reload()
    assert isinstance(_job(sched, 6).trigger, IntervalTrigger)

    # Spec mutates to a cron schedule; reload must replace the trigger.
    rule.compiled_spec = _spec(kind="schedule_only", schedule="0 8 * * *")
    await sched.reload()

    job = _job(sched, 6)
    assert isinstance(job.trigger, CronTrigger)
    assert str(job.trigger) == str(CronTrigger.from_crontab("0 8 * * *"))


async def test_reload_swaps_cron_back_to_interval_when_spec_changes() -> None:
    rule = _FakeRule(
        id=7,
        compiled_spec=_spec(kind="schedule_only", schedule="0 9 * * *"),
        interval_seconds=300,
    )
    rules = [rule]
    sched = _make_scheduler(rules)

    await sched.reload()
    assert isinstance(_job(sched, 7).trigger, CronTrigger)

    rule.compiled_spec = _spec(kind="message_from_user_contains")
    await sched.reload()

    assert isinstance(_job(sched, 7).trigger, IntervalTrigger)


async def test_disabled_rule_job_removed_on_reload() -> None:
    rule = _FakeRule(
        id=8,
        compiled_spec=_spec(kind="schedule_only", schedule="0 9 * * *"),
        interval_seconds=300,
    )
    rules = [rule]
    sched = _make_scheduler(rules)

    await sched.reload()
    assert _job(sched, 8) is not None

    # Rule disappears from the enabled set (disabled/deleted).
    rules.clear()
    await sched.reload()
    assert _job(sched, 8) is None
