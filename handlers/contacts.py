"""Контакты студии и связь с менеджером."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import Settings
from keyboards.callbacks import Menu
from keyboards.catalog import contacts_kb, manager_kb
from keyboards.main import BTN_CONTACTS, BTN_MANAGER
from services.formatting import esc
from utils.flow import reset_flow
from utils.telegram import Event, respond

router = Router(name="contacts")


def contacts_text(settings: Settings) -> str:
    s = settings.studio
    manager = f"@{esc(s.manager_username)}" if s.manager_username else "пока не указан"
    lines = [
        f"📍 <b>{esc(s.name)} — контакты</b>",
        "",
        f"🏢 Адрес: {esc(s.address)}",
        f"🕘 График: {esc(s.work_hours)}",
        f"📞 Телефон: {esc(s.phone)}",
        f"💬 Telegram менеджера: {manager}",
    ]
    if s.demo:
        lines += ["", s.demo_notice]
    return "\n".join(lines)


def manager_text(settings: Settings) -> str:
    s = settings.studio
    if s.manager_url:
        return ("👨‍💼 <b>Связаться с менеджером</b>\n\n"
                "Нажмите кнопку ниже, чтобы открыть чат с менеджером. "
                "Или оставьте заявку — мы свяжемся с вами сами.")
    return ("👨‍💼 <b>Связаться с менеджером</b>\n\n"
            "Прямой контакт менеджера пока не настроен. Оставьте заявку в боте — "
            "менеджер свяжется с вами, чтобы всё уточнить.")


async def show_contacts(event: Event, settings: Settings) -> None:
    await respond(event, contacts_text(settings), contacts_kb(settings.studio))


async def show_manager(event: Event, settings: Settings) -> None:
    await respond(event, manager_text(settings), manager_kb(settings.studio))


@router.message(F.text == BTN_CONTACTS, StateFilter("*"))
@router.message(Command("contacts"), StateFilter("*"))
async def msg_contacts(message: Message, state: FSMContext, settings: Settings) -> None:
    await reset_flow(message, state)
    await show_contacts(message, settings)


@router.callback_query(Menu.filter(F.action == "contacts"), StateFilter("*"))
async def cb_contacts(cb: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    await reset_flow(cb, state)
    await show_contacts(cb, settings)


@router.message(F.text == BTN_MANAGER, StateFilter("*"))
@router.message(Command("manager"), StateFilter("*"))
async def msg_manager(message: Message, state: FSMContext, settings: Settings) -> None:
    await reset_flow(message, state)
    await show_manager(message, settings)


@router.callback_query(Menu.filter(F.action == "manager"), StateFilter("*"))
async def cb_manager(cb: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    await reset_flow(cb, state)
    await show_manager(cb, settings)
