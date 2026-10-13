from datetime import date, timedelta

from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
                           ReplyKeyboardMarkup)

from content.catalog import CAR_CLASSES, SERVICES
from keyboards.callbacks import Book

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


def car_step_kb(cars: list, *, back: bool = True) -> InlineKeyboardMarkup:
    """Шаг «автомобиль»: сохранённые авто — одним нажатием, иначе текстом."""
    rows = [[InlineKeyboardButton(text=f"🚗 {c.title}", callback_data=Book(action="car", value=str(c.id)).pack())]
            for c in cars]
    rows.append(_nav(back))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def date_step_kb(days: list[tuple[date, int]], today: date) -> InlineKeyboardMarkup:
    """Только даты, на которые есть свободное время."""
    buttons = []
    for d, _count in days:
        label = f"{_WEEKDAYS[d.weekday()]} {d.strftime('%d.%m')}"
        if d == today:
            label = "Сегодня"
        elif d == today + timedelta(days=1):
            label = "Завтра"
        buttons.append(InlineKeyboardButton(text=label, callback_data=Book(action="date", value=d.isoformat()).pack()))
    return InlineKeyboardMarkup(inline_keyboard=[*_grid(buttons, 4), _nav()])


def time_step_kb(slots: list) -> InlineKeyboardMarkup:
    """Свободное время начала. «:» в callback_data нельзя: 10:00 -> 1000."""
    buttons = [InlineKeyboardButton(text=f"{s:%H:%M}", callback_data=Book(action="time", value=f"{s:%H%M}").pack())
               for s in slots]
    return InlineKeyboardMarkup(inline_keyboard=[*_grid(buttons, 5), _nav()])


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


def confirm_step_kb(consent: bool, marketing: bool, voucher_offer: tuple[int, bool] | None = None) -> InlineKeyboardMarkup:
    """voucher_offer: (скидка в ₽, применена ли)."""
    mark = "☑️" if consent else "⬜️"
    rows = [
        [InlineKeyboardButton(text=f"{mark} Согласен(на) на обработку персональных данных",
                              callback_data=Book(action="consent").pack())],
        [InlineKeyboardButton(text="📄 Читать согласие", callback_data=Book(action="policy").pack())],
    ]
    if voucher_offer:
        amount, applied = voucher_offer
        text = (f"🎁 Скидка −{amount} ₽ применена (нажмите, чтобы не применять)" if applied
                else f"🎁 Применить скидку −{amount} ₽")
        rows.append([InlineKeyboardButton(text=text, callback_data=Book(action="voucher").pack())])
    rows.append([InlineKeyboardButton(
        text=f"{'☑️' if marketing else '⬜️'} Присылать персональные предложения (по желанию)",
        callback_data=Book(action="marketing").pack())])
    rows.append([InlineKeyboardButton(text="✅ Отправить заявку", callback_data=Book(action="submit").pack())])
    rows.append(_nav())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def policy_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⬅️ Вернуться к заявке", callback_data=Book(action="summary").pack())]])
