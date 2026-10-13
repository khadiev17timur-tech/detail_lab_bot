from datetime import date, datetime, timedelta, timezone

import pytest

from config import ConfigError, load_settings
from content.care import CARE_RULES
from content.catalog import SERVICES_BY_ID
from content.studio import STUDIO
from database.models import Booking, BookingStatus, Voucher
from services.care import recommendations
from services.discounts import best_voucher, discount_amount
from services.planning import care_jobs, reminder_jobs, review_job
from services.validators import ValidationError, parse_date_range

TOKEN = "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
NOW = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------- скидки
@pytest.mark.parametrize("kind,value,price,min_price,expected", [
    ("percent", 10, 2300, 0, 230),
    ("percent", 15, 1999, 0, 299),                 # целочисленное округление вниз, без float
    ("percent", 50, 1, 0, 0),
    ("amount", 500, 2300, 0, 500),
    ("amount", 5000, 2300, 0, 2300),               # скидка не больше цены
    ("amount", 500, 0, 0, 0),
    ("percent", 10, 2300, 5000, 0),                # не дотягивает до минимальной суммы
    ("percent", 10, 5000, 5000, 500),
])
def test_discount_amount(kind, value, price, min_price, expected):
    assert discount_amount(kind, value, price, min_price) == expected


def v(vid, kind, value, expires, status="active", min_price=0):
    return Voucher(vid, 1, 10, "t", kind, value, min_price, "2026-10-01T00:00:00+00:00", expires, status, None)


def test_best_voucher_picks_largest_discount_and_ignores_expired_or_used():
    exp = "2026-12-01T00:00:00+00:00"
    vs = [v(1, "percent", 10, exp), v(2, "amount", 300, exp), v(3, "percent", 50, "2026-01-01T00:00:00+00:00"),
          v(4, "percent", 40, exp, status="used"), v(5, "percent", 5, exp, min_price=10000)]
    voucher, amount = best_voucher(vs, 2300, NOW)
    assert (voucher.id, amount) == (2, 300)
    assert best_voucher([], 2300, NOW) is None and best_voucher(vs[2:], 2300, NOW) is None


# ------------------------------------------------------------ планирование
def test_reminders_skip_those_already_in_the_past():
    studio = STUDIO                                         # 24 ч и 2 ч, Москва (UTC+3)
    start = datetime(2026, 10, 14, 15, 0)                    # 12:00 UTC
    jobs = reminder_jobs(start, NOW, studio)
    assert [(k, d) for k, d in jobs] == [("remind:24", "2026-10-13T12:00:00+00:00"), ("remind:2", "2026-10-14T10:00:00+00:00")]
    near = reminder_jobs(datetime(2026, 10, 12, 13, 0), NOW, studio)          # визит через час (по UTC)
    assert near == []                                        # оба срока уже прошли — ничего не создаём
    assert reminder_jobs(None, NOW, studio) == []


def test_review_and_care_jobs():
    assert review_job(NOW, STUDIO) == ("review", "2026-10-12T12:00:00+00:00")             # +3 часа
    b = _done("polish", "2026-10-01")
    assert [k for k, _ in care_jobs(b, NOW)] == ["care:protect_after_polish"]
    assert care_jobs(_done("headlights", "2026-10-01"), NOW)[0][1] == "2027-10-12T09:00:00+00:00"


# ------------------------------------------------------------ рекомендации
def _done(service, day, bid=1, status=BookingStatus.DONE):
    svc = SERVICES_BY_ID[service]
    return Booking(id=bid, draft_id=f"d{bid}", user_id=10, service_id=service, service_title=svc.title, car="BMW",
                   car_class_id="suv", car_class_title="Внедорожник", visit_date=day, visit_time="10:00", name="И",
                   phone="+7", comment="", estimate_rub=None, addons=(), status=status, consent=True, consent_at=None,
                   created_at="", updated_at="", start_at=f"{day}T10:00")


def test_recommendation_based_only_on_done_visits_and_due_dates():
    today = date(2026, 10, 12)
    recs = recommendations([_done("polish", "2026-10-01")], [], today)
    assert [r.rule.id for r in recs] == ["protect_after_polish"]
    assert recs[0].due == date(2026, 10, 15) and not recs[0].overdue
    late = recommendations([_done("polish", "2026-09-01")], [], today)
    assert late[0].overdue
    assert recommendations([], [], today) == []


def test_recommendation_not_repeated_when_service_already_done_or_booked():
    today = date(2026, 10, 12)
    polish = _done("polish", "2026-09-01", bid=1)
    ceramic_done = _done("ceramic", "2026-09-10", bid=2)
    assert all(r.rule.id != "protect_after_polish" for r in recommendations([polish, ceramic_done], [], today))
    planned = _done("ceramic", "2026-10-20", bid=3, status=BookingStatus.ACCEPTED)
    assert all(r.rule.id != "protect_after_polish" for r in recommendations([polish], [planned], today))


def test_recommendations_use_latest_matching_visit_and_sorted_by_date():
    today = date(2026, 10, 12)
    done = [_done("wash", "2026-08-01", 1), _done("wash", "2026-10-05", 2), _done("polish", "2026-10-01", 3)]
    recs = recommendations(done, [], today)
    wash = next(r for r in recs if r.rule.id == "wash_again")
    assert wash.based_on.id == 2 and wash.due == date(2026, 11, 19)
    assert [r.due for r in recs] == sorted(r.due for r in recs)


def test_every_care_rule_points_to_existing_services():
    for rule in CARE_RULES:
        assert rule.recommend_service in SERVICES_BY_ID and all(s in SERVICES_BY_ID for s in rule.after_services)
        assert rule.days >= 1 and "гарант" not in rule.text.lower()       # не обещаем гарантий


# ---------------------------------------------------------- диапазон дат
def test_parse_date_range():
    today = date(2026, 10, 12)
    assert parse_date_range("25.10", today=today) == (date(2026, 10, 25), date(2026, 10, 25))
    assert parse_date_range("25.10-28.10", today=today) == (date(2026, 10, 25), date(2026, 10, 28))
    assert parse_date_range("25.10.2026 — 28.10.2026", today=today) == (date(2026, 10, 25), date(2026, 10, 28))
    for bad in ("", "abc", "28.10-25.10", "01.01.2020", "25.10-26.10-27.10"):
        with pytest.raises(ValidationError):
            parse_date_range(bad, today=today)


# --------------------------------------------------------------- настройки
def env(**kw):
    base = {"BOT_TOKEN": TOKEN, "ADMIN_IDS": "1"}
    base.update(kw)
    return base


def test_new_studio_settings_from_env():
    s = load_settings(env(STUDIO_POSTS="3", REMINDER_HOURS="48, 24 ,2", REVIEW_URL="https://reviews.example.com/x"))
    assert s.studio.posts == 3 and s.studio.reminder_hours == (48, 24, 2)
    assert s.studio.review_url == "https://reviews.example.com/x"
    d = load_settings(env())
    assert d.studio.posts == 2 and d.studio.reminder_hours == (24, 2) and d.studio.review_url == ""


@pytest.mark.parametrize("bad", [{"STUDIO_POSTS": "0"}, {"STUDIO_POSTS": "abc"}, {"REMINDER_HOURS": "x"},
                                 {"REMINDER_HOURS": "0"}, {"REMINDER_HOURS": "9999"}, {"REVIEW_URL": "ftp://x"}])
def test_bad_new_settings_are_rejected(bad):
    with pytest.raises(ConfigError):
        load_settings(env(**bad))
