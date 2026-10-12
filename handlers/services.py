"""Каталог услуг и карточка услуги."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import Settings
from content.catalog import SERVICES_BY_ID
from keyboards.callbacks import Menu, Svc
from keyboards.catalog import catalog_kb, service_kb
from keyboards.main import BTN_SERVICES
from services.formatting import catalog_text, service_card
from utils.flow import reset_flow
from utils.telegram import Event, ack, respond

router = Router(name="services")


def _notice(settings: Settings) -> str:
    return settings.studio.demo_notice if settings.studio.demo else ""


async def show_catalog(event: Event, settings: Settings) -> None:
    await respond(event, catalog_text(_notice(settings)), catalog_kb())


@router.message(F.text == BTN_SERVICES, StateFilter("*"))
@router.message(Command("services"), StateFilter("*"))
async def msg_services(message: Message, state: FSMContext, settings: Settings) -> None:
    await reset_flow(message, state)
    await show_catalog(message, settings)


@router.callback_query(Menu.filter(F.action == "services"), StateFilter("*"))
async def cb_services(cb: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    await reset_flow(cb, state)
    await show_catalog(cb, settings)


@router.callback_query(Svc.filter(F.action == "view"), StateFilter("*"))
async def cb_service_view(cb: CallbackQuery, callback_data: Svc, state: FSMContext, settings: Settings) -> None:
    service = SERVICES_BY_ID.get(callback_data.id)
    if service is None:
        await ack(cb, "Эта услуга больше недоступна. Откройте каталог заново.", alert=True)
        return
    await reset_flow(cb, state)
    await respond(cb, service_card(service, _notice(settings)), service_kb(service))
