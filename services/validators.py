"""Проверка и нормализация вводимых клиентом данных.

Все функции либо возвращают очищенное значение, либо бросают ValidationError
с текстом, который можно показать пользователю.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta


class ValidationError(ValueError):
    """Сообщение предназначено для показа пользователю."""


ANY_TIME = "any"
_SPACES = re.compile(r"\s+")
_NAME_RE = re.compile(r"^[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё '’.\-]*$")
_CAR_RE = re.compile(r"^[\w\s.,()/+'’\-]+$", re.UNICODE)


def _collapse(raw: str) -> str:
    return _SPACES.sub(" ", raw or "").strip()


# ------------------------------------------------------------------ телефон
def normalize_phone(raw: str) -> str:
    """Приводит номер к виду +7XXXXXXXXXX (или +<международный>)."""
    s = (raw or "").strip()
    if not s or len(s) > 30 or not re.fullmatch(r"\+?[\d\s()\-.]+", s):
        raise ValidationError("Номер должен содержать только цифры, например: +7 999 123-45-67.")
    digits = re.sub(r"\D", "", s)
    has_plus = s.startswith("+")

    if not has_plus and len(digits) == 11 and digits[0] in "78":
        national = digits[1:]
    elif not has_plus and len(digits) == 10:
        national = digits
    elif has_plus and digits.startswith("7") and len(digits) == 11:
        national = digits[1:]
    elif has_plus and 11 <= len(digits) <= 15 and not digits.startswith(("0", "7", "89")):
        return "+" + digits  # международный номер
    else:
        raise ValidationError("Не удалось распознать номер. Пример: +7 999 123-45-67.")

    if national[0] not in "3456789":
        raise ValidationError("Похоже, в номере опечатка. Пример: +7 999 123-45-67.")
    return "+7" + national


def format_phone(phone: str) -> str:
    """+79991234567 -> +7 999 123-45-67 (для красивого показа)."""
    m = re.fullmatch(r"\+7(\d{3})(\d{3})(\d{2})(\d{2})", phone or "")
    return f"+7 {m[1]} {m[2]}-{m[3]}-{m[4]}" if m else phone


# --------------------------------------------------------------- имя, авто, текст
def validate_name(raw: str) -> str:
    name = _collapse(raw)
    if len(name) < 2:
        raise ValidationError("Имя слишком короткое. Напишите, как к вам обращаться.")
    if len(name) > 50:
        raise ValidationError("Имя слишком длинное — не более 50 символов.")
    if not _NAME_RE.match(name):
        raise ValidationError("В имени допустимы только буквы, пробел, дефис и апостроф.")
    return name


def validate_car(raw: str) -> str:
    car = _collapse(raw)
    if len(car) < 2:
        raise ValidationError("Укажите марку и модель, например: BMW X5.")
    if len(car) > 60:
        raise ValidationError("Слишком длинное название — не более 60 символов.")
    if not re.search(r"[A-Za-zА-Яа-яЁё]", car) or not _CAR_RE.match(car):
        raise ValidationError("Укажите марку и модель буквами, например: Toyota Camry.")
    return car


def validate_comment(raw: str, max_len: int = 500) -> str:
    comment = (raw or "").strip()
    if len(comment) > max_len:
        raise ValidationError(f"Комментарий слишком длинный: {len(comment)} из {max_len} символов. Сократите, пожалуйста.")
    return comment


# --------------------------------------------------------------------- дата
_DATE_WORDS = {"сегодня": 0, "завтра": 1, "послезавтра": 2}


def parse_visit_date(raw: str, *, today: date, horizon_days: int = 90) -> date:
    """Принимает ДД.ММ, ДД.ММ.ГГГГ, ГГГГ-ММ-ДД, а также «сегодня/завтра/послезавтра»."""
    s = _collapse(raw).lower()
    if s in _DATE_WORDS:
        result = today + timedelta(days=_DATE_WORDS[s])
    else:
        result = None
        iso = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
        dmy = re.fullmatch(r"(\d{1,2})[./\-](\d{1,2})(?:[./\-](\d{2}|\d{4}))?", s)
        try:
            if iso:
                result = date(int(iso[1]), int(iso[2]), int(iso[3]))
            elif dmy:
                day, month, year = int(dmy[1]), int(dmy[2]), dmy[3]
                if year is None:
                    result = date(today.year, month, day)
                    if result < today:  # «05.01» в декабре — это следующий год
                        result = date(today.year + 1, month, day)
                else:
                    y = int(year)
                    result = date(2000 + y if y < 100 else y, month, day)
        except ValueError:
            result = None
        if result is None:
            raise ValidationError("Не получилось распознать дату. Напишите, например: 25.10 или «завтра».")
    if result < today:
        raise ValidationError("Эта дата уже прошла. Выберите сегодняшнюю или более позднюю дату.")
    if result > today + timedelta(days=horizon_days):
        raise ValidationError(f"Мы принимаем заявки максимум на {horizon_days} дней вперёд. Выберите ближайшую дату.")
    return result


# ------------------------------------------------------------------- время
def _parse_hhmm(text: str) -> time | None:
    m = re.fullmatch(r"(\d{1,2})(?:[:.\s]?(\d{2}))?", text)
    if not m:
        return None
    hour, minute = int(m[1]), int(m[2] or 0)
    try:
        return time(hour, minute)
    except ValueError:
        return None


def parse_visit_time(raw: str, *, on_date: date, now: datetime,
                     opens: str = "10:00", closes: str = "20:00") -> str:
    """Возвращает «ЧЧ:ММ» или ANY_TIME. Проверяет часы работы и «время уже прошло» для сегодняшнего дня."""
    s = _collapse(raw).lower()
    if s in {ANY_TIME, "любое", "любое время", "в любое время"}:
        return ANY_TIME
    parsed = _parse_hhmm(s)
    if parsed is None:
        raise ValidationError("Напишите время в формате ЧЧ:ММ, например 14:30.")
    open_t, close_t = _parse_hhmm(opens), _parse_hhmm(closes)
    assert open_t and close_t
    if not (open_t <= parsed < close_t):
        raise ValidationError(f"Студия работает с {opens} до {closes}. Выберите время в этом интервале.")
    if on_date == now.date() and parsed <= now.time().replace(tzinfo=None):
        raise ValidationError("Это время сегодня уже прошло. Выберите более позднее.")
    return parsed.strftime("%H:%M")


# --------------------------------------------------------------- отображение
_WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


def format_date_ru(d: date | str) -> str:
    if isinstance(d, str):
        d = date.fromisoformat(d)
    return f"{_WEEKDAYS[d.weekday()]}, {d.strftime('%d.%m.%Y')}"


def format_time_ru(value: str) -> str:
    return "в любое время" if value == ANY_TIME else value
