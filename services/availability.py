"""Свободное время для клиента: связывает расписание (services.schedule) с базой."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from content.catalog import Service
from database.repository import Repository
from services import schedule


def local_now(studio, now: datetime | None = None) -> datetime:
    """Текущее «настенное» время студии без tzinfo (так хранятся start_at/end_at)."""
    tz = ZoneInfo(studio.timezone)
    moment = now.astimezone(tz) if now is not None and now.tzinfo else (now or datetime.now(tz))
    return moment.replace(tzinfo=None) if moment.tzinfo else moment


async def _context(repo: Repository, studio, service: Service, first_day: date, last_day: date):
    window_end = datetime.combine(last_day + timedelta(days=service.days + 1), datetime.min.time())
    busy = await repo.busy_intervals(datetime.combine(first_day, datetime.min.time()), window_end)
    blocks = await repo.block_ranges(first_day)
    return busy, blocks


async def dates_with_slots(repo: Repository, studio, service: Service, now_local: datetime, *,
                           limit: int = 8, ignore: schedule.Interval | None = None,
                           horizon_days: int | None = None) -> list[tuple[date, int]]:
    horizon = horizon_days if horizon_days is not None else studio.booking_horizon_days
    busy, blocks = await _context(repo, studio, service, now_local.date(), now_local.date() + timedelta(days=horizon))
    return schedule.next_days_with_slots(service, busy, blocks, now_local, studio,
                                         horizon_days=horizon, limit=limit, ignore=ignore)


async def slots_for_day(repo: Repository, studio, service: Service, day: date, now_local: datetime, *,
                        ignore: schedule.Interval | None = None) -> list[datetime]:
    busy, blocks = await _context(repo, studio, service, day, day)
    return schedule.free_slots(day, service, busy, blocks, now_local, studio, ignore=ignore)


def interval_of(service: Service, start: datetime, studio) -> tuple[datetime, datetime]:
    return start, schedule.service_end(service, start, studio)


def local_now_date_default() -> date:
    """Сегодняшняя дата без привязки к настройкам студии (для выборок «с сегодняшнего дня»)."""
    return date.today()
