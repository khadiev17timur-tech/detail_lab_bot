"""Портфолио: карточки типовых работ, фото берутся из assets/portfolio/."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message

from config import Settings
from content.portfolio import PORTFOLIO_BY_ID, PortfolioItem, find_photo
from keyboards.callbacks import Menu, Port
from keyboards.catalog import portfolio_card_kb, portfolio_kb
from keyboards.main import BTN_PORTFOLIO
from services.formatting import esc
from utils.flow import reset_flow
from utils.telegram import Event, ack, message_of, respond

log = logging.getLogger(__name__)
router = Router(name="portfolio")

CAPTION_LIMIT = 1024


def card_text(item: PortfolioItem, settings: Settings) -> str:
    mark = ("🧪 <i>Демо-карточка: пример типовой работы, а не реальный проект студии.</i>"
            if settings.studio.demo else "<i>Пример типовой работы.</i>")
    works = "\n".join(f"• {esc(w)}" for w in item.works)
    return (f"{item.emoji} <b>{esc(item.title)}</b>\n{mark}\n\n"
            f"<b>Результат</b>\n{esc(item.result)}\n\n<b>Выполненные работы</b>\n{works}")


async def show_list(event: Event, settings: Settings) -> None:
    text = "📸 <b>Портфолио</b>\nВыберите тип работ, чтобы посмотреть пример:"
    if settings.studio.demo:
        text += "\n\n" + settings.studio.demo_notice
    await respond(event, text, portfolio_kb())


@router.message(F.text == BTN_PORTFOLIO, StateFilter("*"))
@router.message(Command("portfolio"), StateFilter("*"))
async def msg_portfolio(message: Message, state: FSMContext, settings: Settings) -> None:
    await reset_flow(message, state)
    await show_list(message, settings)


@router.callback_query(Menu.filter(F.action == "portfolio"), StateFilter("*"))
async def cb_portfolio(cb: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    await reset_flow(cb, state)
    await show_list(cb, settings)


@router.callback_query(Port.filter(F.action == "view"), StateFilter("*"))
async def cb_card(cb: CallbackQuery, callback_data: Port, state: FSMContext, settings: Settings) -> None:
    item = PORTFOLIO_BY_ID.get(callback_data.id)
    if item is None:
        await ack(cb, "Эта карточка больше недоступна.", alert=True)
        return
    await reset_flow(cb, state)
    text = card_text(item, settings)
    photo = find_photo(settings.portfolio_dir, item.id)
    msg = message_of(cb)
    if photo is not None and msg is not None and len(text) <= CAPTION_LIMIT:
        try:
            await ack(cb)
            await msg.answer_photo(FSInputFile(photo), caption=text, reply_markup=portfolio_card_kb())
            try:
                await msg.delete()
            except TelegramAPIError:
                pass
            return
        except TelegramAPIError as exc:
            log.warning("Не удалось отправить фото %s: %s — показываю текст", photo.name, exc)
    await respond(cb, text, portfolio_card_kb())
