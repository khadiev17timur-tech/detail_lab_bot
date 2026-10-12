"""Клавиатуры услуг, прайса и портфолио/FAQ."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from content.catalog import SERVICES, Service
from content.faq import FAQ
from content.portfolio import PORTFOLIO
from keyboards.callbacks import Faq, Menu, Port, Svc
from keyboards.main import home_button


def _service_buttons(action_factory) -> list[list[InlineKeyboardButton]]:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for s in SERVICES:
        row.append(InlineKeyboardButton(text=f"{s.emoji} {s.short_title}", callback_data=action_factory(s)))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows


def catalog_kb() -> InlineKeyboardMarkup:
    rows = _service_buttons(lambda s: Svc(action="view", id=s.id).pack())
    rows.append([InlineKeyboardButton(text="💰 Прайс-лист", callback_data=Menu(action="prices").pack()),
                 InlineKeyboardButton(text="🧮 Калькулятор", callback_data=Menu(action="calc").pack())])
    rows.append([home_button()])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def prices_kb() -> InlineKeyboardMarkup:
    rows = _service_buttons(lambda s: Svc(action="view", id=s.id).pack())
    rows.append([InlineKeyboardButton(text="🧮 Рассчитать стоимость", callback_data=Menu(action="calc").pack())])
    rows.append([InlineKeyboardButton(text="🗓 Записаться", callback_data=Menu(action="book").pack()), home_button()])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def service_kb(service: Service) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗓 Записаться на услугу", callback_data=Svc(action="book", id=service.id).pack())],
        [InlineKeyboardButton(text="🧮 Рассчитать стоимость", callback_data=Svc(action="calc", id=service.id).pack())],
        [InlineKeyboardButton(text="⬅️ К каталогу", callback_data=Menu(action="services").pack()), home_button()],
    ])


def portfolio_kb() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"{p.emoji} {p.title}", callback_data=Port(action="view", id=p.id).pack())]
            for p in PORTFOLIO]
    rows.append([home_button()])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def portfolio_card_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗓 Записаться", callback_data=Menu(action="book").pack())],
        [InlineKeyboardButton(text="⬅️ К портфолио", callback_data=Menu(action="portfolio").pack()), home_button()],
    ])


def faq_kb() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=item.question, callback_data=Faq(action="view", id=item.id).pack())]
            for item in FAQ]
    rows.append([home_button()])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def faq_answer_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ К вопросам", callback_data=Menu(action="faq").pack()),
         InlineKeyboardButton(text="🗓 Записаться", callback_data=Menu(action="book").pack())],
        [home_button()],
    ])


def contacts_kb(studio) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if studio.manager_url:
        rows.append([InlineKeyboardButton(text="💬 Написать менеджеру", url=studio.manager_url)])
    link_row: list[InlineKeyboardButton] = []
    if studio.map_url:
        link_row.append(InlineKeyboardButton(text="🗺 Открыть карту", url=studio.map_url))
    if studio.website_url:
        link_row.append(InlineKeyboardButton(text="🌐 Сайт", url=studio.website_url))
    if link_row:
        rows.append(link_row)
    rows.append([InlineKeyboardButton(text="🗓 Записаться", callback_data=Menu(action="book").pack())])
    rows.append([home_button()])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def manager_kb(studio) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if studio.manager_url:
        rows.append([InlineKeyboardButton(text="💬 Открыть чат с менеджером", url=studio.manager_url)])
    rows.append([InlineKeyboardButton(text="🗓 Оставить заявку", callback_data=Menu(action="book").pack())])
    rows.append([home_button()])
    return InlineKeyboardMarkup(inline_keyboard=rows)
