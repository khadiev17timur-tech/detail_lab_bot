"""Логика заявки: проверка черновика, сборка записи для БД."""
from __future__ import annotations

from datetime import date, datetime
from typing import Mapping

from content.catalog import CAR_CLASSES_BY_ID, SERVICES_BY_ID
from database.models import NewBooking
from services.pricing import PricingError, calculate_estimate
from services.validators import (ANY_TIME, ValidationError, normalize_phone,
                                 validate_car, validate_comment, validate_name)

# Обязательные поля черновика в порядке прохождения шагов
REQUIRED_FIELDS = ("service_id", "car", "car_class", "visit_date", "visit_time", "name", "phone")

# Какому шагу сценария соответствует поле (чтобы вернуть пользователя к ошибке)
FIELD_TO_STEP = {
    "service_id": "service", "car": "car", "car_class": "car_class",
    "visit_date": "date", "visit_time": "time", "name": "name", "phone": "phone",
}


def find_problems(draft: Mapping) -> list[str]:
    """Возвращает список полей черновика, которые пусты или некорректны."""
    problems: list[str] = []
    for field in REQUIRED_FIELDS:
        value = draft.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            problems.append(field)

    def bad(field: str, check) -> None:
        if field in problems:
            return
        try:
            check(draft[field])
        except (ValidationError, ValueError, KeyError):
            problems.append(field)

    def check_service(v):
        if v not in SERVICES_BY_ID:
            raise ValueError

    def check_class(v):
        if v not in CAR_CLASSES_BY_ID:
            raise ValueError

    def check_date(v):
        date.fromisoformat(v)

    def check_time(v):
        if v != ANY_TIME:
            datetime.strptime(v, "%H:%M")

    bad("service_id", check_service)
    bad("car", validate_car)
    bad("car_class", check_class)
    bad("visit_date", check_date)
    bad("visit_time", check_time)
    bad("name", validate_name)
    bad("phone", normalize_phone)
    try:
        validate_comment(draft.get("comment") or "")
    except ValidationError:
        problems.append("comment")
    return problems


def first_problem_step(problems: list[str]) -> str | None:
    for field in REQUIRED_FIELDS:
        if field in problems:
            return FIELD_TO_STEP[field]
    return "comment" if "comment" in problems else None


def build_new_booking(draft: Mapping, *, user_id: int) -> NewBooking:
    """Собирает NewBooking из черновика. Бросает ValueError, если данные неполные."""
    problems = find_problems(draft)
    if problems:
        raise ValueError(f"Некорректные поля заявки: {', '.join(problems)}")
    if not draft.get("consent"):
        raise ValueError("Нет согласия на обработку персональных данных")
    draft_id = draft.get("draft_id")
    if not draft_id:
        raise ValueError("У черновика нет draft_id")

    service = SERVICES_BY_ID[draft["service_id"]]
    car_class = CAR_CLASSES_BY_ID[draft["car_class"]]
    try:
        estimate = calculate_estimate(service.id, car_class.id, draft.get("addon_ids") or ())
        estimate_rub, addons = estimate.total, estimate.addon_titles
    except PricingError:
        estimate_rub, addons = None, ()

    return NewBooking(
        draft_id=str(draft_id), user_id=user_id,
        service_id=service.id, service_title=service.title,
        car=validate_car(draft["car"]),
        car_class_id=car_class.id, car_class_title=car_class.title,
        visit_date=draft["visit_date"], visit_time=draft["visit_time"],
        name=validate_name(draft["name"]), phone=normalize_phone(draft["phone"]),
        comment=validate_comment(draft.get("comment") or ""),
        estimate_rub=estimate_rub, addons=addons, consent=True,
    )
