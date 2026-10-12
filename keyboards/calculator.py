from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from content.catalog import ADDONS_BY_ID, CAR_CLASSES, SERVICES, Service
from keyboards.callbacks import Calc
from keyboards.main import home_button
from services.pricing import format_coef, format_rub


def calc_services_kb() -> InlineKeyboardMarkup:
    rows, row = [], []
    for s in SERVICES:
        row.append(InlineKeyboardButton(text=f"{s.emoji} {s.short_title}", callback_data=Calc(action="svc", value=s.id).pack()))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([home_button()])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def calc_classes_kb() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"{c.title} ({format_coef(c.coef_pct)})",
                                  callback_data=Calc(action="cls", value=c.id).pack())] for c in CAR_CLASSES]
    rows.append([InlineKeyboardButton(text="⬅️ К услугам", callback_data=Calc(action="restart").pack()), home_button()])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def calc_addons_kb(service: Service, selected: set[str]) -> InlineKeyboardMarkup:
    rows = []
    for addon_id in service.addon_ids:
        addon = ADDONS_BY_ID[addon_id]
        mark = "☑️" if addon_id in selected else "⬜️"
        rows.append([InlineKeyboardButton(
            text=f"{mark} {addon.title} · +{format_rub(addon.price)}",
            callback_data=Calc(action="addon", value=addon_id).pack())])
    rows.append([InlineKeyboardButton(text="🧮 Рассчитать", callback_data=Calc(action="done").pack())])
    rows.append([InlineKeyboardButton(text="⬅️ К классу авто", callback_data=Calc(action="back").pack()), home_button()])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def calc_result_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Оставить заявку", callback_data=Calc(action="book").pack())],
        [InlineKeyboardButton(text="🔄 Рассчитать заново", callback_data=Calc(action="restart").pack()), home_button()],
    ])
