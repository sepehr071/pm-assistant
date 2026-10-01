"""APScheduler wrapper that keeps one job per enabled `Rule`.

One scheduler instance lives on the FastAPI app.state; lifespan calls
`start()` at app boot (which also reloads existing rules from the DB) and
`stop()` on shutdown. The rules API calls `reload()` after any mutation.

Trigger selection: a rule whose compiled `filter.schedule` is a non-empty
cron expression (e.g. ``"0 9 * * 1-5"``) runs on a `CronTrigger`; every
other rule falls back to an `IntervalTrigger(interval_seconds)`. An invalid
cron string degrades gracefully to the interval trigger (logged warning, no
crash, rule not disabled).

All scheduling concerns (job id convention, floor interval, grace time, max
instances, error-swallowing job wrapper) live here so `rule_engine.tick` can
assume it's invoked in a well-behaved async context.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from db.models import Rule, RuleFiring
from services import rule_engine

logger = logging.getLogger(__name__)


_MIN_INTERVAL_SECONDS = 60

# Cron jobs can legitimately fire hours apart (e.g. "weekdays 09:00"), so a
# tight misfire window would skip a tick whenever the event loop was briefly
# busy at the fire time. One hour is generous enough to absorb that without
# letting a missed daily run pile up indefinitely.
_CRON_MISFIRE_GRACE_SECONDS = 3600

# Retention policy for the `rulefiring` audit table. One tick per rule
# per interval can pile up fast (5 rules at 60s = ~7k rows/day); prune
# older rows once a day so timeline queries stay fast and the DB file
# stops growing without bound. Tuneable via `rule_firing_retention_days`
# on the settings object if the user wants a longer/shorter window.
_DEFAULT_FIRING_RETENTION_DAYS = 30
_FIRING_PRUNE_JOB_ID = "rule-firing-prune"


class RuleScheduler:
    """Owns the underlying `AsyncIOScheduler` plus the bind of (mcp, llm,
    settings, db_factory, default_model) that every `rule_engine.tick` needs.

    Public surface consumed by `main.py` + `api/rules.py`:
      - `await scheduler.start()`            # call from lifespan startup
      - `await scheduler.stop()`             # call from lifespan shutdown
      - `await scheduler.reload()`           # call after any rule CRUD
    """

    def __init__(
        self,
        mcp: Any,
        llm: Any,
        settings: Any,
        db_factory: async_sessionmaker[AsyncSession],
        default_model: str,
    ) -> None:
        self.mcp = mcp
        self.llm = llm
        self.settings = settings
        self.db_factory = db_factory
        self.default_model = default_model
        self._scheduler = AsyncIOScheduler()
        self._started = False

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._started:
            return
        # Reload before starting so existing rules get jobs registered on the
        # first tick cycle, not after a 60s wait.
        await self.reload()
        self._register_firing_prune_job()
        self._scheduler.start()
        self._started = True
        logger.info(
            "RuleScheduler started with %d active jobs",
            len(self._scheduler.get_jobs()),
        )

    async def stop(self) -> None:
        if not self._started:
            return
        self._scheduler.shutdown(wait=False)
        self._started = False
        logger.info("RuleScheduler stopped")

    # ------------------------------------------------------------------
    # reload — sync job table from DB
    # ------------------------------------------------------------------

    async def reload(self) -> None:
        """Read all enabled rules and sync the APScheduler job table.

        - Add jobs for newly-created enabled rules.
        - Update jobs whose interval changed.
        - Remove jobs for rules that have been disabled or deleted.
        """
        async with self.db_factory() as session:
            rules = (
                await session.execute(select(Rule).where(Rule.enabled.is_(True)))
            ).scalars().all()

        wanted_ids: set[int] = set()
        for rule in rules:
            if rule.id is None:
                continue
            wanted_ids.add(rule.id)
            trigger, misfire_grace = self._build_trigger(rule)
            job_id = f"rule-{rule.id}"
            # `replace_existing=True` rebuilds the job from the current trigger
            # on every reload, so a cron<->interval swap (or a changed cron
            # expression / interval) is picked up without any manual diff.
            self._scheduler.add_job(
                self._run_tick,
                trigger=trigger,
                id=job_id,
                kwargs={"rule_id": rule.id},
                max_instances=1,
                misfire_grace_time=misfire_grace,
                coalesce=True,
                replace_existing=True,
            )

        # Remove jobs for rules that are no longer enabled / present.
        for job in list(self._scheduler.get_jobs()):
            if not job.id.startswith("rule-"):
                continue
            try:
                rid = int(job.id.split("-", 1)[1])
            except ValueError:
                continue
            if rid not in wanted_ids:
                self._scheduler.remove_job(job.id)

    # ------------------------------------------------------------------
    # trigger selection
    # ------------------------------------------------------------------

    def _build_trigger(self, rule: Rule) -> tuple[Any, int]:
        """Pick the trigger for a rule and its misfire grace window.

        Returns ``(CronTrigger, _CRON_MISFIRE_GRACE_SECONDS)`` when the
        compiled `filter.schedule` is a usable cron expression; otherwise
        ``(IntervalTrigger, interval_seconds)``. A malformed spec or an
        invalid cron string degrades to the interval trigger with a logged
        warning — never crashes `reload()` and never disables the rule.
        """
        interval = max(int(rule.interval_seconds or 0), _MIN_INTERVAL_SECONDS)
        interval_trigger = IntervalTrigger(seconds=interval)

        schedule = self._extract_schedule(rule)
        if not schedule:
            return interval_trigger, interval

        try:
            # No explicit timezone: APScheduler interprets the cron expression
            # in server-local time. Deliberate for this local-first single-user
            # app — "every weekday 9am" means the user's 9am, not 09:00 UTC.
            return CronTrigger.from_crontab(schedule), _CRON_MISFIRE_GRACE_SECONDS
        except (ValueError, TypeError) as exc:
            logger.warning(
                "Rule %s has invalid cron schedule %r (%s); "
                "falling back to interval trigger (%ss)",
                rule.id,
                schedule,
                exc,
                interval,
            )
            return interval_trigger, interval

    @staticmethod
    def _extract_schedule(rule: Rule) -> str | None:
        """Pull a non-empty `filter.schedule` cron string out of the rule's
        compiled spec JSON, tolerating malformed/legacy specs by returning
        None (caller then uses the interval trigger)."""
        raw = getattr(rule, "compiled_spec", None)
        if not raw:
            return None
        try:
            spec = json.loads(raw)
        except (ValueError, TypeError):
            logger.warning(
                "Rule %s has unparseable compiled_spec; using interval trigger",
                getattr(rule, "id", "?"),
            )
            return None
        if not isinstance(spec, dict):
            return None
        filter_ = spec.get("filter")
        if not isinstance(filter_, dict):
            return None
        schedule = filter_.get("schedule")
        if isinstance(schedule, str) and schedule.strip():
            return schedule.strip()
        return None

    # ------------------------------------------------------------------
    # job callback
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # firing retention prune
    # ------------------------------------------------------------------

    def _register_firing_prune_job(self) -> None:
        """Register a daily cron job that deletes `RuleFiring` rows older
        than the retention window. Idempotent — safe to call from
        `start()` and from `reload()` if we ever need to refresh the
        schedule."""
        retention_days = int(
            getattr(
                self.settings,
                "rule_firing_retention_days",
                _DEFAULT_FIRING_RETENTION_DAYS,
            )
        )
        if retention_days <= 0:
            # Retention disabled — make sure no stale prune job remains.
            existing = self._scheduler.get_job(_FIRING_PRUNE_JOB_ID)
            if existing is not None:
                self._scheduler.remove_job(_FIRING_PRUNE_JOB_ID)
            return
        # Run at 03:17 daily (off the hour to avoid clashing with other
        # cron jobs the user may add later).
        self._scheduler.add_job(
            self._prune_firings,
            trigger=CronTrigger(hour=3, minute=17),
            id=_FIRING_PRUNE_JOB_ID,
            kwargs={"retention_days": retention_days},
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )

    async def _prune_firings(self, retention_days: int) -> None:
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        try:
            async with self.db_factory() as session:
                result = await session.execute(
                    delete(RuleFiring).where(RuleFiring.fired_at < cutoff)
                )
                await session.commit()
            deleted = result.rowcount or 0
            if deleted:
                logger.info(
                    "Pruned %d rule firings older than %d days",
                    deleted,
                    retention_days,
                )
        except Exception:
            logger.exception("Failed to prune RuleFiring rows")

    async def _run_tick(self, rule_id: int) -> None:
        try:
            await rule_engine.tick(
                rule_id,
                mcp=self.mcp,
                llm=self.llm,
                settings=self.settings,
                db_factory=self.db_factory,
                default_model=self.default_model,
            )
        except Exception:
            # rule_engine.tick is supposed to swallow its own errors; belt +
            # suspenders here so a bug in the engine can never take down the
            # scheduler thread.
            logger.exception("rule_engine.tick raised for rule %s", rule_id)
