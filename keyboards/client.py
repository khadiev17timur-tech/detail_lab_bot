"""Клавиатуры личного кабинета клиента."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from aiogram.types import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Markup

from database.models import Booking, Car
from keyboards.callbacks import Cab, CarCb, Menu, Mine, Rev, RevC
from keyboards.main import home_button
from services.formatting import my_booking_label

_WD = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


def _grid(buttons: list[Btn], per_row: int) -> list[list[Btn]]:
    return [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]


def cabinet_kb() -> Markup:
    return Markup(inline_keyboard=[
        [Btn(text="📅 Мои записи", callback_data=Cab(a="bookings").pack()),
         Btn(text="🚗 Мой автомобиль", callback_data=Cab(a="cars").pack())],
        [Btn(text="🎁 Скидки и предложения", callback_data=Cab(a="offers").pack())],
        [Btn(text="🔔 Уведомления", callback_data=Cab(a="notify").pack())],
        [Btn(text="🗓 Новая запись", callback_data=Menu(action="book").pack()), home_button()],
    ])


def back_to_cabinet() -> Btn:
    return Btn(text="⬅️ В кабинет", callback_data=Cab(a="home").pack())


def my_bookings_kb(bookings: list[Booking]) -> Markup:
    rows = [[Btn(text=my_booking_label(b), callback_data=Mine(a="view", id=b.id).pack())] for b in bookings]
    rows.append([Btn(text="🗓 Новая запись", callback_data=Menu(action="book").pack()), back_to_cabinet()])
    return Markup(inline_keyboard=rows)


def booking_card_kb(b: Booking) -> Markup:
    rows = []
    if b.is_active:
        first = [Btn(text="🔁 Перенести", callback_data=Mine(a="resched", id=b.id).pack()),
                 Btn(text="❌ Отменить", callback_data=Mine(a="cancel", id=b.id).pack())]
        rows.append(first)
        if not b.client_confirmed_at and b.status.value in ("in_work", "accepted"):
            rows.insert(0, [Btn(text="✅ Приду", callback_data=Mine(a="ok", id=b.id).pack())])
    rows.append([Btn(text="⬅️ К записям", callback_data=Cab(a="bookings").pack()), home_button()])
    return Markup(inline_keyboard=rows)


def reminder_kb(b: Booking) -> Markup:
    return Markup(inline_keyboard=[
        [Btn(text="✅ Приду", callback_data=Mine(a="ok", id=b.id).pack())],
        [Btn(text="🔁 Перенести", callback_data=Mine(a="resched", id=b.id).pack()),
         Btn(text="❌ Отменить", callback_data=Mine(a="cancel", id=b.id).pack())],
    ])


def cancel_confirm_kb(booking_id: int) -> Markup:
    return Markup(inline_keyboard=[
        [Btn(text="Да, отменить запись", callback_data=Mine(a="cancel_yes", id=booking_id).pack())],
        [Btn(text="⬅️ Оставить как есть", callback_data=Mine(a="view", id=booking_id).pack())],
    ])


def _day_label(d: date, today: date) -> str:
    if d == today:
        return "Сегодня"
    if d == today + timedelta(days=1):
        return "Завтра"
    return f"{_WD[d.weekday()]} {d:%d.%m}"


def resched_dates_kb(booking_id: int, days: list[tuple[date, int]], today: date) -> Markup:
    buttons = [Btn(text=_day_label(d, today), callback_data=Mine(a="rdate", id=booking_id, v=d.isoformat()).pack())
               for d, _ in days]
    rows = _grid(buttons, 4)
    rows.append([Btn(text="⬅️ Назад", callback_data=Mine(a="view", id=booking_id).pack())])
    return Markup(inline_keyboard=rows)


def resched_times_kb(booking_id: int, day: date, slots: list[datetime]) -> Markup:
    buttons = [Btn(text=f"{s:%H:%M}", callback_data=Mine(a="rtime", id=booking_id, v=f"{day.isoformat()}_{s:%H%M}").pack())
               for s in slots]
    rows = _grid(buttons, 5)
    rows.append([Btn(text="⬅️ Другая дата", callback_data=Mine(a="resched", id=booking_id).pack())])
    return Markup(inline_keyboard=rows)


def rating_kb(booking_id: int) -> Markup:
    return Markup(inline_keyboard=[[Btn(text=f"{n}⭐", callback_data=Rev(id=booking_id, r=n).pack()) for n in range(1, 6)]])


def review_comment_kb(booking_id: int) -> Markup:
    return Markup(inline_keyboard=[[Btn(text="Пропустить", callback_data=RevC(a="skip", id=booking_id).pack())]])


def review_link_kb(url: str) -> Markup | None:
    return Markup(inline_keyboard=[[Btn(text="⭐ Оставить отзыв публично", url=url)]]) if url else None


def cars_kb(cars: list[Car]) -> Markup:
    rows = [[Btn(text=f"🚗 {c.title}", callback_data=CarCb(a="view", id=c.id).pack())] for c in cars]
    rows.append([Btn(text="➕ Добавить автомобиль", callback_data=CarCb(a="add").pack())])
    rows.append([back_to_cabinet(), home_button()])
    return Markup(inline_keyboard=rows)


def car_card_kb(car_id: int) -> Markup:
    return Markup(inline_keyboard=[
        [Btn(text="🗓 Записаться", callback_data=Menu(action="book").pack())],
        [Btn(text="🗑 Убрать из списка", callback_data=CarCb(a="del", id=car_id).pack())],
        [Btn(text="⬅️ К автомобилям", callback_data=Cab(a="cars").pack()), home_button()],
    ])


def car_delete_confirm_kb(car_id: int) -> Markup:
    return Markup(inline_keyboard=[
        [Btn(text="Да, убрать", callback_data=CarCb(a="del_yes", id=car_id).pack())],
        [Btn(text="Отмена", callback_data=CarCb(a="view", id=car_id).pack())],
    ])


def offers_kb() -> Markup:
    return Markup(inline_keyboard=[
        [Btn(text="🗓 Записаться со скидкой", callback_data=Menu(action="book").pack())],
        [back_to_cabinet(), home_button()],
    ])


def notify_kb(enabled: bool) -> Markup:
    toggle = (Btn(text="🔕 Отключить предложения", callback_data=Cab(a="notify_off").pack()) if enabled
              else Btn(text="🔔 Включить предложения", callback_data=Cab(a="notify_on").pack()))
    return Markup(inline_keyboard=[[toggle], [back_to_cabinet(), home_button()]])
