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


class Cab(CallbackData, prefix="cab"):
    a: str            # home, bookings, cars, offers, notify, notify_on, notify_off


class Mine(CallbackData, prefix="mine"):
    a: str            # view, ok, cancel, cancel_yes, resched, rdate, rtime
    id: int = 0       # номер записи
    v: str = ""       # дата 2026-10-20 или 2026-10-20_1200


class Rev(CallbackData, prefix="rev"):
    id: int           # номер записи
    r: int            # оценка 1..5


class RevC(CallbackData, prefix="revc"):
    a: str            # skip
    id: int


class CarCb(CallbackData, prefix="car"):
    a: str            # view, del, del_yes, add
    id: int = 0


class AdmX(CallbackData, prefix="ax"):
    a: str            # day, blocks, block_add, block_del, reviews, analytics, rules, rule_on, rule_off, rule_new, rule_*
    id: int = 0
    v: str = ""
