"""Прайс-лист."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import Settings
from keyboards.callbacks import Menu
from keyboards.catalog import prices_kb
from keyboards.main import BTN_PRICES
from services.formatting import price_list_text
from utils.flow import reset_flow
from utils.telegram import Event, respond

router = Router(name="prices")


async def show_prices(event: Event, settings: Settings) -> None:
    notice = settings.studio.demo_notice if settings.studio.demo else ""
    await respond(event, price_list_text(notice), prices_kb())


@router.message(F.text == BTN_PRICES, StateFilter("*"))
@router.message(Command("prices"), StateFilter("*"))
async def msg_prices(message: Message, state: FSMContext, settings: Settings) -> None:
    await reset_flow(message, state)
    await show_prices(message, settings)


@router.callback_query(Menu.filter(F.action == "prices"), StateFilter("*"))
async def cb_prices(cb: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    await reset_flow(cb, state)
    await show_prices(cb, settings)
