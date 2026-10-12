from datetime import date, datetime, timedelta

import pytest

from services.booking import build_new_booking, find_problems, first_problem_step
from services.validators import (ANY_TIME, ValidationError, format_date_ru, format_phone,
                                 normalize_phone, parse_visit_date, parse_visit_time,
                                 validate_car, validate_comment, validate_name)

TODAY = date(2026, 10, 12)  # понедельник
NOW = datetime(2026, 10, 12, 14, 0)


@pytest.mark.parametrize("raw,expected", [
    ("+7 999 123-45-67", "+79991234567"),
    ("89991234567", "+79991234567"),
    ("8 (999) 123-45-67", "+79991234567"),
    ("79991234567", "+79991234567"),
    ("9991234567", "+79991234567"),
    ("+7(999)1234567", "+79991234567"),
    ("  +7 999 123 45 67  ", "+79991234567"),
    ("+7 495 123-45-67", "+74951234567"),
    ("+380501234567", "+380501234567"),
])
def test_phone_valid(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", [
    "", "   ", "12345", "abc", "+7 999 123-45", "+7 999 123-45-678", "899912345",
    "+70000000000", "+7 (000) 000-00-00", "99912345678901234567890123456789",
    "+7999-123-45-6x", "++79991234567", "+89991234567", "<script>",
])
def test_phone_invalid(raw):
    with pytest.raises(ValidationError):
        normalize_phone(raw)


def test_phone_pretty():
    assert format_phone("+79991234567") == "+7 999 123-45-67"
    assert format_phone("+380501234567") == "+380501234567"


@pytest.mark.parametrize("raw,expected", [("  иван  ", "иван"), ("Анна-Мария", "Анна-Мария"),
                                          ("John  O'Neil", "John O'Neil"), ("Ёлка", "Ёлка")])
def test_name_valid(raw, expected):
    assert validate_name(raw) == expected


@pytest.mark.parametrize("raw", ["", "А", "1234", "Иван123", "x" * 51, "😀😀", "<b>Ivan</b>"])
def test_name_invalid(raw):
    with pytest.raises(ValidationError):
        validate_name(raw)


@pytest.mark.parametrize("raw", ["BMW X5", "Toyota Camry 2.5", "Lada (Vesta) SW", "Mercedes-Benz E 200", "Лада Гранта"])
def test_car_valid(raw):
    assert validate_car(raw)


@pytest.mark.parametrize("raw", ["", "1", "12345", "x" * 61, "<script>alert(1)</script>", "Audi\x00"])
def test_car_invalid(raw):
    with pytest.raises(ValidationError):
        validate_car(raw)


def test_comment():
    assert validate_comment("  привет ") == "привет"
    assert validate_comment("") == ""
    with pytest.raises(ValidationError):
        validate_comment("x" * 501)


@pytest.mark.parametrize("raw,expected", [
    ("25.10", date(2026, 10, 25)), ("25.10.2026", date(2026, 10, 25)), ("25/10/26", date(2026, 10, 25)),
    ("2026-10-25", date(2026, 10, 25)), ("сегодня", TODAY), ("Завтра", TODAY + timedelta(days=1)),
    ("послезавтра", TODAY + timedelta(days=2)), ("1.11", date(2026, 11, 1)),
])
def test_date_valid(raw, expected):
    assert parse_visit_date(raw, today=TODAY) == expected


@pytest.mark.parametrize("raw", ["", "вчера", "32.01", "30.02", "11.11.1111", "abc", "01.01.2020",
                                 "2027-12-01", "25.13"])
def test_date_invalid(raw):
    with pytest.raises(ValidationError):
        parse_visit_date(raw, today=TODAY, horizon_days=90)


def test_date_without_year_rolls_to_next_year_only_if_in_horizon():
    assert parse_visit_date("05.01", today=date(2026, 12, 20), horizon_days=90) == date(2027, 1, 5)


@pytest.mark.parametrize("raw,expected", [("15:30", "15:30"), ("9:00", "10:00" if False else "09:00"),
                                          ("1530", "15:30"), ("15.30", "15:30"), ("16", "16:00"),
                                          ("любое время", ANY_TIME), ("any", ANY_TIME)])
def test_time_valid_future_day(raw, expected):
    day = TODAY + timedelta(days=1)
    if raw == "9:00":  # до открытия студии
        with pytest.raises(ValidationError):
            parse_visit_time(raw, on_date=day, now=NOW)
        return
    assert parse_visit_time(raw, on_date=day, now=NOW) == expected


@pytest.mark.parametrize("raw", ["", "25:00", "12:60", "abc", "20:00", "23:30", "07:00", "12345"])
def test_time_invalid(raw):
    with pytest.raises(ValidationError):
        parse_visit_time(raw, on_date=TODAY + timedelta(days=1), now=NOW)


def test_time_today_cannot_be_in_the_past():
    with pytest.raises(ValidationError):
        parse_visit_time("12:00", on_date=TODAY, now=NOW)
    assert parse_visit_time("18:00", on_date=TODAY, now=NOW) == "18:00"


def test_format_date_ru():
    assert format_date_ru("2026-10-12") == "пн, 12.10.2026"


# ------------------------------------------------- обязательные поля заявки
def good_draft(**over):
    d = {"draft_id": "abc", "service_id": "polish", "car": "BMW X5", "car_class": "suv",
         "visit_date": "2026-10-20", "visit_time": "12:00", "name": "Иван", "phone": "+79991234567",
         "comment": "", "consent": True, "addon_ids": []}
    d.update(over)
    return d


def test_complete_draft_has_no_problems_and_builds():
    assert find_problems(good_draft()) == []
    nb = build_new_booking(good_draft(), user_id=7)
    assert nb.user_id == 7 and nb.service_title.startswith("Полировка") and nb.estimate_rub == 27000
    assert nb.phone == "+79991234567" and nb.car_class_title == "Внедорожник"


@pytest.mark.parametrize("field", ["service_id", "car", "car_class", "visit_date", "visit_time", "name", "phone"])
def test_each_required_field_is_enforced(field):
    draft = good_draft()
    del draft[field]
    assert field in find_problems(draft)
    assert find_problems(good_draft(**{field: "  "})) == [field] or field in find_problems(good_draft(**{field: "  "}))
    with pytest.raises(ValueError):
        build_new_booking(draft, user_id=1)


def test_invalid_values_are_flagged_and_first_step_found():
    problems = find_problems(good_draft(service_id="zzz", phone="123", visit_date="31.31.2026", visit_time="25:61"))
    assert set(problems) == {"service_id", "phone", "visit_date", "visit_time"}
    assert first_problem_step(problems) == "service"
    assert first_problem_step(["phone", "name"]) == "name"
    assert first_problem_step([]) is None


def test_consent_is_mandatory_for_building():
    with pytest.raises(ValueError):
        build_new_booking(good_draft(consent=False), user_id=1)
    with pytest.raises(ValueError):
        build_new_booking(good_draft(draft_id=""), user_id=1)


def test_bad_addons_do_not_break_booking_but_drop_estimate():
    nb = build_new_booking(good_draft(addon_ids=["ozone"]), user_id=1)  # ozone не для полировки
    assert nb.estimate_rub is None and nb.addons == ()
