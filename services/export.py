"""Выгрузка заявок в CSV (кодировка UTF-8 с BOM и «;» — открывается в Excel)."""
from __future__ import annotations

import csv
import io
from typing import Iterable

from database.models import STATUS_LABELS, Booking
from services.validators import format_date_ru, format_time_ru

CSV_COLUMNS = (
    "Номер", "Статус", "Создана (UTC)", "Обновлена (UTC)", "Услуга", "Автомобиль",
    "Класс авто", "Желаемая дата", "Желаемое время", "Имя", "Телефон",
    "Telegram ID", "Username", "Комментарий", "Предв. стоимость, ₽", "Доп. работы",
    "Начало", "Окончание", "Скидка, ₽", "Кем отменена",
)
# Username не включаем: у Telegram-ников нет спецсимволов, а «@» мы добавляем сами.
_TEXT_COLUMNS_DANGEROUS = {"Автомобиль", "Имя", "Комментарий", "Услуга", "Доп. работы"}
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _safe(value: str) -> str:
    """Защита от CSV-инъекций: ячейка не должна начинаться с символа формулы."""
    return "'" + value if value.startswith(_FORMULA_PREFIXES) else value


def bookings_to_csv(bookings: Iterable[Booking]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for b in bookings:
        row = {
            "Номер": b.id,
            "Статус": STATUS_LABELS[b.status],
            "Создана (UTC)": b.created_at,
            "Обновлена (UTC)": b.updated_at,
            "Услуга": b.service_title,
            "Автомобиль": b.car,
            "Класс авто": b.car_class_title,
            "Желаемая дата": format_date_ru(b.visit_date),
            "Желаемое время": format_time_ru(b.visit_time),
            "Имя": b.name,
            "Телефон": b.phone,
            "Telegram ID": b.user_id,
            "Username": f"@{b.username}" if b.username else "",
            "Комментарий": b.comment,
            "Предв. стоимость, ₽": b.estimate_rub if b.estimate_rub is not None else "",
            "Доп. работы": ", ".join(b.addons),
            "Начало": b.start_at or "",
            "Окончание": b.end_at or "",
            "Скидка, ₽": b.discount_rub or "",
            "Кем отменена": b.cancelled_by or "",
        }
        writer.writerow([_safe(str(v)) if k in _TEXT_COLUMNS_DANGEROUS else v for k, v in row.items()])
    return buf.getvalue().encode("utf-8-sig")
