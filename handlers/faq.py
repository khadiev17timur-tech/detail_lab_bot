"""Частые вопросы."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from content.faq import FAQ_BY_ID
from keyboards.callbacks import Faq, Menu
from keyboards.catalog import faq_answer_kb, faq_kb
from keyboards.main import BTN_FAQ
from services.formatting import esc
from utils.flow import reset_flow
from utils.telegram import Event, ack, respond

router = Router(name="faq")


async def show_faq(event: Event) -> None:
    await respond(event, "❓ <b>Частые вопросы</b>\nВыберите вопрос:", faq_kb())


@router.message(F.text == BTN_FAQ, StateFilter("*"))
@router.message(Command("faq"), StateFilter("*"))
async def msg_faq(message: Message, state: FSMContext) -> None:
    await reset_flow(message, state)
    await show_faq(message)


@router.callback_query(Menu.filter(F.action == "faq"), StateFilter("*"))
async def cb_faq(cb: CallbackQuery, state: FSMContext) -> None:
    await reset_flow(cb, state)
    await show_faq(cb)


@router.callback_query(Faq.filter(F.action == "view"), StateFilter("*"))
async def cb_answer(cb: CallbackQuery, callback_data: Faq, state: FSMContext) -> None:
    item = FAQ_BY_ID.get(callback_data.id)
    if item is None:
        await ack(cb, "Этот вопрос больше недоступен.", alert=True)
        return
    await reset_flow(cb, state)
    await respond(cb, f"❓ <b>{esc(item.question)}</b>\n\n{esc(item.answer)}", faq_answer_kb())
