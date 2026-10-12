"""Типизированные callback-данные (aiogram CallbackData)."""
from aiogram.filters.callback_data import CallbackData


class Menu(CallbackData, prefix="menu"):
    action: str  # home, services, prices, calc, book, portfolio, faq, contacts, manager


class Svc(CallbackData, prefix="svc"):
    action: str  # view, book, calc
    id: str = ""


class Calc(CallbackData, prefix="calc"):
    action: str  # svc, cls, addon, done, back, restart, book
    value: str = ""


class Book(CallbackData, prefix="book"):
    action: str  # svc, cls, date, time, usename, skip, consent, policy, summary, submit, back, cancel
    value: str = ""


class Port(CallbackData, prefix="port"):
    action: str  # list, view
    id: str = ""


class Faq(CallbackData, prefix="faq"):
    action: str  # list, view
    id: str = ""


class Adm(CallbackData, prefix="adm"):
    a: str            # home, list, last, view, set, stats, csv, filters, close
    id: int = 0       # номер заявки
    s: str = ""       # целевой статус (для set)
    f: str = ""       # фильтр списка / ожидаемый статус (для set)
    p: int = 0        # страница
