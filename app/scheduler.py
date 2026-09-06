"""Scheduler interno (APScheduler). Alternativa: systemd timer + scripts/run_job.py."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from . import jobs
from .config import settings

log = logging.getLogger(__name__)

# Yahoo limita por IP y el bloqueo puede durar bastante. Si el job no consigue
# ni un solo precio, se reintenta mas tarde en lugar de perder el dia.
RETRY_DELAY_MIN = 45
MAX_RETRIES = 2

_scheduler: BackgroundScheduler | None = None


def _job(retry: int = 0) -> None:
    try:
        stats = jobs.run_daily_job()
    except Exception:
        log.exception("el job programado ha fallado")
        return

    if stats.get("con_precio", 0) == 0 and retry < MAX_RETRIES and _scheduler is not None:
        cuando = datetime.now(_scheduler.timezone) + timedelta(minutes=RETRY_DELAY_MIN)
        log.warning(
            "ningun precio obtenido; reintento %d/%d a las %s",
            retry + 1, MAX_RETRIES, cuando.strftime("%H:%M"),
        )
        _scheduler.add_job(
            _job, trigger="date", run_date=cuando, args=[retry + 1],
            id=f"retry_{retry + 1}", replace_existing=True,
        )


def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(timezone=settings.timezone)
    _scheduler.add_job(
        _job,
        trigger="cron",
        day_of_week="mon-fri",
        hour=settings.job_hour,
        minute=settings.job_minute,
        id="daily_portfolio_job",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    _scheduler.start()
    log.info(
        "scheduler activo: lunes a viernes a las %02d:%02d (%s)",
        settings.job_hour, settings.job_minute, settings.timezone,
    )


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
