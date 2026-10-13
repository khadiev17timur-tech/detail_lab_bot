from dataclasses import replace
from datetime import date, datetime, timedelta

from content.catalog import SERVICES_BY_ID
from content.studio import STUDIO
from services.schedule import (candidate_starts, free_slots, has_capacity, is_day_open, max_concurrency,
                               next_days_with_slots, service_end)

D = date(2026, 10, 20)           # вторник
NOW = datetime(2026, 10, 19, 9, 0)
WASH = SERVICES_BY_ID["wash"]            # 60 минут
INTERIOR = SERVICES_BY_ID["interior"]    # 8 часов
POLISH = SERVICES_BY_ID["polish"]        # 2 дня
ST = replace(STUDIO, posts=1)


def dt(h, m=0, day=D):
    return datetime(day.year, day.month, day.day, h, m)


def test_grid_for_short_service_respects_closing_time():
    slots = candidate_starts(D, WASH, ST)
    assert slots[0] == dt(10) and slots[-1] == dt(19) and len(slots) == 10
    # 8-часовая услуга: последний старт, чтобы закончить до 20:00, — 12:00
    assert [s.hour for s in candidate_starts(D, INTERIOR, ST)] == [10, 11, 12]


def test_multi_day_service_starts_only_at_opening_and_blocks_until_last_day_close():
    assert candidate_starts(D, POLISH, ST) == [dt(10)]
    assert service_end(POLISH, dt(10), ST) == dt(20, day=D + timedelta(days=1))


def test_single_post_blocks_overlapping_slots():
    busy = [(dt(12), dt(13))]
    slots = [s.hour for s in free_slots(D, WASH, busy, [], NOW, ST)]
    assert 12 not in slots and 11 in slots and 13 in slots


def test_long_service_cannot_overlap_existing_booking():
    busy = [(dt(14), dt(15))]
    assert [s.hour for s in free_slots(D, INTERIOR, busy, [], NOW, ST)] == []   # любой 8-часовой старт заденет 14:00


def test_two_posts_allow_two_parallel_bookings_but_not_three():
    st2 = replace(STUDIO, posts=2)
    busy = [(dt(12), dt(13))]
    assert 12 in [s.hour for s in free_slots(D, WASH, busy, [], NOW, st2)]
    busy2 = busy + [(dt(12), dt(13))]
    assert 12 not in [s.hour for s in free_slots(D, WASH, busy2, [], NOW, st2)]


def test_concurrency_is_not_overcounted_for_sequential_bookings():
    # две последовательные записи по одному посту НЕ блокируют окно 10-12 при двух постах
    busy = [(dt(10), dt(11)), (dt(11), dt(12))]
    assert max_concurrency(busy, (dt(10), dt(12))) == 1
    assert has_capacity(busy, dt(10), dt(12), posts=2)
    assert not has_capacity(busy + [(dt(10, 30), dt(11, 30))], dt(10), dt(12), posts=2)


def test_touching_intervals_do_not_conflict():
    assert has_capacity([(dt(10), dt(11))], dt(11), dt(12), posts=1)


def test_past_and_lead_time_are_excluded_today():
    now = datetime(2026, 10, 20, 14, 30)
    slots = [s.hour for s in free_slots(D, WASH, [], [], now, ST)]
    assert slots[0] == 16            # 14:30 + 60 минут запаса = 15:30 → первый целый слот 16:00
    assert free_slots(D, WASH, [], [], datetime(2026, 10, 20, 19, 30), ST) == []


def test_closed_weekday_and_blocked_dates():
    sunday = date(2026, 10, 25)
    st = replace(ST, closed_weekdays=(6,))
    assert not is_day_open(sunday, st, [])
    assert free_slots(sunday, WASH, [], [], NOW, st) == []
    assert free_slots(D, WASH, [], [(D, D)], NOW, ST) == []
    # многодневная услуга не должна пересекать закрытую дату внутри срока
    assert free_slots(D, POLISH, [], [(D + timedelta(days=1), D + timedelta(days=1))], NOW, ST) == []


def test_reschedule_ignores_own_interval():
    own = (dt(12), dt(13))
    assert 12 not in [s.hour for s in free_slots(D, WASH, [own], [], NOW, ST)]
    assert 12 in [s.hour for s in free_slots(D, WASH, [own], [], NOW, ST, ignore=own)]


def test_next_days_with_slots_skips_full_and_closed_days():
    full_day = [(dt(10, day=D), dt(20, day=D))]
    days = next_days_with_slots(WASH, full_day, [], NOW, ST, horizon_days=5, limit=3)
    assert D not in [d for d, _ in days] and len(days) == 3
