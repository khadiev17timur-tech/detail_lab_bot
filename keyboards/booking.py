from datetime import date, timedelta

from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
                           ReplyKeyboardMarkup)

from content.catalog import CAR_CLASSES, SERVICES
from keyboards.callbacks import Book
from services.validators import ANY_TIME

BTN_BACK = "⬅️ Назад"
BTN_CANCEL = "❌ Отмена"
BTN_SHARE_CONTACT = "📱 Поделиться контактом"

_WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


def _nav(back: bool = True) -> list[InlineKeyboardButton]:
    row = []
    if back:
        row.append(InlineKeyboardButton(text=BTN_BACK, callback_data=Book(action="back").pack()))
    row.append(InlineKeyboardButton(text=BTN_CANCEL, callback_data=Book(action="cancel").pack()))
    return row


def _grid(buttons: list[InlineKeyboardButton], per_row: int) -> list[list[InlineKeyboardButton]]:
    return [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]


def service_step_kb() -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(text=f"{s.emoji} {s.short_title}",
                                    callback_data=Book(action="svc", value=s.id).pack()) for s in SERVICES]
    return InlineKeyboardMarkup(inline_keyboard=[*_grid(buttons, 2), _nav(back=False)])


def text_step_kb(*, back: bool = True) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[_nav(back)])


def name_step_kb(tg_name: str | None, *, back: bool = True) -> InlineKeyboardMarkup:
    rows = []
    if tg_name:
        rows.append([InlineKeyboardButton(text=f"Использовать «{tg_name[:30]}»",
                                          callback_data=Book(action="usename").pack())])
    rows.append(_nav(back))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def class_step_kb() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"{c.title} — {c.examples}",
                                  callback_data=Book(action="cls", value=c.id).pack())] for c in CAR_CLASSES]
    rows.append(_nav())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def date_step_kb(today: date, days: int = 8, *, include_today: bool = True) -> InlineKeyboardMarkup:
    buttons = []
    for i in range(0 if include_today else 1, days):
        d = today + timedelta(days=i)
        label = f"{_WEEKDAYS[d.weekday()]} {d.strftime('%d.%m')}"
        if i == 0:
            label = "Сегодня"
        elif i == 1:
            label = "Завтра"
        buttons.append(InlineKeyboardButton(text=label, callback_data=Book(action="date", value=d.isoformat()).pack()))
    return InlineKeyboardMarkup(inline_keyboard=[*_grid(buttons, 4), _nav()])


def time_step_kb(times: tuple[str, ...]) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(text=t, callback_data=Book(action="time", value=t.replace(":", "")).pack())
               for t in times]  # «:» в callback_data нельзя: 10:00 -> 1000
    rows = _grid(buttons, 5)
    rows.append([InlineKeyboardButton(text="🕘 Любое время", callback_data=Book(action="time", value=ANY_TIME).pack())])
    rows.append(_nav())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def phone_step_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_SHARE_CONTACT, request_contact=True)],
                  [KeyboardButton(text=BTN_BACK), KeyboardButton(text=BTN_CANCEL)]],
        resize_keyboard=True, one_time_keyboard=False,
        input_field_placeholder="+7 999 123-45-67",
    )


def comment_step_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Пропустить", callback_data=Book(action="skip").pack())],
        _nav(),
    ])


def confirm_step_kb(consent: bool) -> InlineKeyboardMarkup:
    mark = "☑️" if consent else "⬜️"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{mark} Согласен(на) на обработку персональных данных",
                              callback_data=Book(action="consent").pack())],
        [InlineKeyboardButton(text="📄 Читать согласие", callback_data=Book(action="policy").pack())],
        [InlineKeyboardButton(text="✅ Отправить заявку", callback_data=Book(action="submit").pack())],
        _nav(),
    ])


def policy_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⬅️ Вернуться к заявке", callback_data=Book(action="summary").pack())]])
