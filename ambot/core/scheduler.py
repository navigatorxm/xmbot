"""
BotScheduler — thin APScheduler wrapper.

Provides two job types:
- interval_job: runs every N seconds (used by reconciler, health checks)
- cron_job: calendar-based (used by monthly commission sweep)
"""
from __future__ import annotations

import logging
from typing import Any, Callable

log = logging.getLogger("ambot.scheduler")


class BotScheduler:
    """Wraps APScheduler's AsyncIOScheduler."""

    def __init__(self) -> None:
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
        except ImportError as exc:
            raise ImportError("apscheduler is required: pip install apscheduler") from exc

        self._scheduler = AsyncIOScheduler(timezone="UTC")

    def add_interval_job(
        self,
        func: Callable,
        seconds: int,
        job_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Register a job that runs every `seconds` seconds."""
        jid = job_id or f"interval_{func.__name__}"
        self._scheduler.add_job(
            func,
            trigger="interval",
            seconds=seconds,
            id=jid,
            replace_existing=True,
            **kwargs,
        )
        log.info("Registered interval job '%s' (every %ds)", jid, seconds)

    def add_cron_job(
        self,
        func: Callable,
        job_id: str | None = None,
        **cron_kwargs: Any,
    ) -> None:
        """Register a cron-style job. Pass day, hour, minute etc. as kwargs."""
        jid = job_id or f"cron_{func.__name__}"
        self._scheduler.add_job(
            func,
            trigger="cron",
            id=jid,
            replace_existing=True,
            **cron_kwargs,
        )
        log.info("Registered cron job '%s' (%s)", jid, cron_kwargs)

    async def start(self) -> None:
        self._scheduler.start()
        log.info("Scheduler started")

    async def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            log.info("Scheduler stopped")
