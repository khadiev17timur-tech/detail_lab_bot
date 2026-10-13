"""Календарь студии: свободные слоты, занятость постов, закрытые даты.

Чистые функции без базы и Telegram — всё время «настенное» (naive) в часовом поясе студии.
Занятость считается по пересечению интервалов и числу постов (`studio.posts`): запись допустима,
если в любой момент её интервала одновременно занято меньше `posts` постов.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Iterable, Sequence

from content.catalog import Service

Interval = tuple[datetime, datetime]
DateRange = tuple[date, date]
ISO_MIN = "%Y-%m-%dT%H:%M"


def parse_hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def to_iso(dt: datetime) -> str:
    return dt.strftime(ISO_MIN)


def from_iso(value: str) -> datetime:
    return datetime.strptime(value, ISO_MIN)


def open_dt(day: date, studio) -> datetime:
    return datetime.combine(day, parse_hhmm(studio.open_time))


def close_dt(day: date, studio) -> datetime:
    return datetime.combine(day, parse_hhmm(studio.close_time))


def service_end(service: Service, start: datetime, studio) -> datetime:
    """Когда освободится пост. Многодневные услуги держат пост до закрытия последнего дня."""
    if service.days > 1:
        return close_dt(start.date() + timedelta(days=service.days - 1), studio)
    return start + timedelta(minutes=service.duration_min)


def overlaps(a: Interval, b: Interval) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def max_concurrency(busy: Iterable[Interval], window: Interval) -> int:
    """Максимальное число одновременно занятых постов внутри окна."""
    events: list[tuple[datetime, int]] = []
    for start, end in busy:
        if not overlaps((start, end), window):
            continue
        events.append((max(start, window[0]), 1))
        events.append((min(end, window[1]), -1))
    # при равенстве времени сначала освобождаем (-1), потом занимаем (+1)
    events.sort(key=lambda e: (e[0], e[1]))
    current = peak = 0
    for _, delta in events:
        current += delta
        peak = max(peak, current)
    return peak


def has_capacity(busy: Iterable[Interval], start: datetime, end: datetime, posts: int) -> bool:
    return max_concurrency(busy, (start, end)) < posts


def is_blocked(first: date, last: date, blocks: Sequence[DateRange]) -> bool:
    return any(b_from <= last and first <= b_to for b_from, b_to in blocks)


def is_day_open(day: date, studio, blocks: Sequence[DateRange]) -> bool:
    return day.weekday() not in studio.closed_weekdays and not is_blocked(day, day, blocks)


def candidate_starts(day: date, service: Service, studio) -> list[datetime]:
    """Сетка начала визитов в день `day` (без учёта занятости)."""
    first = open_dt(day, studio)
    if service.days > 1:
        return [first]                      # многодневные услуги принимаются на открытии
    step = timedelta(minutes=studio.slot_step_min)
    last_end = close_dt(day, studio)
    slots, cur = [], first
    while cur + timedelta(minutes=service.duration_min) <= last_end:
        slots.append(cur)
        cur += step
    return slots


def free_slots(day: date, service: Service, busy: Sequence[Interval], blocks: Sequence[DateRange],
               now: datetime, studio, *, ignore: Interval | None = None) -> list[datetime]:
    """Свободное время начала для услуги в указанный день.

    `ignore` — интервал переносимой записи: он не считается занятым (перенос на соседнее время).
    """
    if not is_day_open(day, studio, blocks):
        return []
    effective = [b for b in busy if b != ignore] if ignore else list(busy)
    earliest = now + timedelta(minutes=studio.min_lead_minutes)
    result = []
    for start in candidate_starts(day, service, studio):
        if start < earliest:
            continue
        end = service_end(service, start, studio)
        if service.days > 1 and is_blocked(start.date(), end.date(), blocks):
            continue
        if has_capacity(effective, start, end, studio.posts):
            result.append(start)
    return result


def next_days_with_slots(service: Service, busy: Sequence[Interval], blocks: Sequence[DateRange],
                         now: datetime, studio, *, horizon_days: int, limit: int = 8,
                         ignore: Interval | None = None) -> list[tuple[date, int]]:
    """Ближайшие даты, где есть свободное время: [(дата, сколько слотов)]."""
    found: list[tuple[date, int]] = []
    for offset in range(0, horizon_days + 1):
        day = now.date() + timedelta(days=offset)
        slots = free_slots(day, service, busy, blocks, now, studio, ignore=ignore)
        if slots:
            found.append((day, len(slots)))
            if len(found) >= limit:
                break
    return found
