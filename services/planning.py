"""Планирование заданий: напоминания, запрос отзыва, рекомендации по уходу."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from content.care import CARE_RULES, CareRule
from database.models import Booking
from database.repository import JobSpec, iso_utc


def _local_to_utc(local_naive: datetime, tz_name: str) -> datetime:
    return local_naive.replace(tzinfo=ZoneInfo(tz_name)).astimezone(timezone.utc)


def reminder_jobs(start_local: datetime | None, now_utc: datetime, studio) -> list[JobSpec]:
    """Напоминания за N часов до визита. Те, чьё время уже прошло, не создаются."""
    if start_local is None:
        return []
    start_utc = _local_to_utc(start_local, studio.timezone)
    jobs = []
    for hours in sorted(set(studio.reminder_hours), reverse=True):
        due = start_utc - timedelta(hours=hours)
        if due > now_utc:
            jobs.append((f"remind:{hours}", iso_utc(due)))
    return jobs


def review_job(now_utc: datetime, studio) -> JobSpec:
    return ("review", iso_utc(now_utc + timedelta(hours=studio.review_delay_hours)))


def care_jobs(booking: Booking, now_utc: datetime, rules: tuple[CareRule, ...] = CARE_RULES) -> list[JobSpec]:
    """Рекомендации по уходу после завершённой услуги (по правилам студии)."""
    jobs = []
    for rule in rules:
        if booking.service_id in rule.after_services:
            due = now_utc + timedelta(days=rule.days)
            jobs.append((f"care:{rule.id}", iso_utc(due)))
    return jobs


def care_due_date(done_on: date, rule: CareRule) -> date:
    return done_on + timedelta(days=rule.days)
