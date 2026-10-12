"""Главное меню и общие кнопки навигации."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from keyboards.callbacks import Book, Menu

BTN_SERVICES = "🚘 Услуги"
BTN_PRICES = "💰 Цены"
BTN_BOOK = "🗓 Записаться"
BTN_CALC = "🧮 Рассчитать стоимость"
BTN_PORTFOLIO = "📸 Портфолио"
BTN_FAQ = "❓ Частые вопросы"
BTN_CONTACTS = "📍 Контакты"
BTN_MANAGER = "👨‍💼 Связаться с менеджером"

MENU_TEXTS = frozenset({BTN_SERVICES, BTN_PRICES, BTN_BOOK, BTN_CALC, BTN_PORTFOLIO,
                        BTN_FAQ, BTN_CONTACTS, BTN_MANAGER})


def main_menu() -> ReplyKeyboardMarkup:
    rows = [
        [BTN_SERVICES, BTN_PRICES],
        [BTN_BOOK, BTN_CALC],
        [BTN_PORTFOLIO, BTN_FAQ],
        [BTN_CONTACTS],
        [BTN_MANAGER],
    ]
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t) for t in row] for row in rows],
        resize_keyboard=True,
        input_field_placeholder="Выберите раздел",
    )


def home_button() -> InlineKeyboardButton:
    return InlineKeyboardButton(text="🏠 В меню", callback_data=Menu(action="home").pack())


def home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[home_button()]])


def new_request_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🗓 Новая заявка", callback_data=Book(action="new").pack()),
    ]])
