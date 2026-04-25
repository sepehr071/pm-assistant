"""APScheduler wrapper that keeps one interval job per enabled `Rule`.

One scheduler instance lives on the FastAPI app.state; lifespan calls
`start()` at app boot (which also reloads existing rules from the DB) and
`stop()` on shutdown. The rules API calls `reload()` after any mutation.

All scheduling concerns (job id convention, floor interval, grace time, max
instances, error-swallowing job wrapper) live here so `rule_engine.tick` can
assume it's invoked in a well-behaved async context.
"""
from __future__ import annotations

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
            interval = max(int(rule.interval_seconds or 0), _MIN_INTERVAL_SECONDS)
            job_id = f"rule-{rule.id}"
            self._scheduler.add_job(
                self._run_tick,
                trigger=IntervalTrigger(seconds=interval),
                id=job_id,
                kwargs={"rule_id": rule.id},
                max_instances=1,
                misfire_grace_time=interval,
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
