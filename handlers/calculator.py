"""Пошаговый калькулятор предварительной стоимости."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from content.catalog import CAR_CLASSES_BY_ID, SERVICES_BY_ID
from database.repository import Repository
from handlers.booking import start_booking
from keyboards.callbacks import Calc, Menu, Svc
from keyboards.calculator import calc_addons_kb, calc_classes_kb, calc_result_kb, calc_services_kb
from keyboards.main import BTN_CALC
from config import Settings
from services.formatting import PRICE_DISCLAIMER, esc
from services.pricing import Estimate, PricingError, calculate_estimate, format_coef, format_rub
from states.calculator import CalcStates
from utils.flow import reset_flow
from utils.telegram import Event, ack, respond

log = logging.getLogger(__name__)
router = Router(name="calculator")

STALE = "Эта кнопка устарела. Запустите расчёт заново: «🧮 Рассчитать стоимость»."


def render_estimate(est: Estimate) -> str:
    lines = [
        "🧮 <b>Предварительный расчёт</b>",
        "",
        f"Услуга: <b>{esc(est.service_title)}</b>",
        f"Класс автомобиля: {esc(est.car_class_title)} ({format_coef(est.coef_pct)})",
        "",
        "<b>Состав расчёта</b>",
        f"• Базовая стоимость: от {format_rub(est.base_price)} {format_coef(est.coef_pct)} = {format_rub(est.class_price)}",
    ]
    for line in est.addons:
        lines.append(f"• {esc(line.title)}: +{format_rub(line.amount)}")
    lines += ["", f"💰 <b>Итого: от {format_rub(est.total)}</b>", "", PRICE_DISCLAIMER]
    return "\n".join(lines)


async def start_calc(event: Event, state: FSMContext, service_id: str | None = None) -> None:
    await reset_flow(event, state)
    if service_id and service_id in SERVICES_BY_ID:
        await state.set_state(CalcStates.car_class)
        await state.update_data(service_id=service_id, addons=[])
        await _ask_class(event, service_id)
        return
    await state.set_state(CalcStates.service)
    await respond(event, "🧮 <b>Калькулятор стоимости</b>\n\nШаг 1. Выберите услугу:", calc_services_kb())


async def _ask_class(event: Event, service_id: str) -> None:
    svc = SERVICES_BY_ID[service_id]
    await respond(
        event,
        f"🧮 <b>{esc(svc.title)}</b>\n\nШаг 2. Выберите класс автомобиля — от него зависит стоимость:",
        calc_classes_kb(),
    )


async def _ask_addons(event: Event, service_id: str, selected: set[str]) -> None:
    svc = SERVICES_BY_ID[service_id]
    await respond(
        event,
        f"🧮 <b>{esc(svc.title)}</b>\n\nШаг 3. Отметьте дополнительные работы (необязательно), "
        "затем нажмите «Рассчитать»:",
        calc_addons_kb(svc, selected),
    )


async def _show_result(event: Event, state: FSMContext) -> None:
    data = await state.get_data()
    try:
        est = calculate_estimate(data["service_id"], data["car_class"], data.get("addons") or [])
    except (KeyError, PricingError):
        log.warning("Некорректные данные калькулятора: %s", data)
        await state.clear()
        await respond(event, "Не удалось выполнить расчёт. Попробуйте начать заново.", calc_services_kb())
        await state.set_state(CalcStates.service)
        return
    await state.set_state(CalcStates.result)
    await respond(event, render_estimate(est), calc_result_kb())


# ----------------------------------------------------------------- вход
@router.message(F.text == BTN_CALC, StateFilter("*"))
@router.message(Command("calc"), StateFilter("*"))
async def msg_calc(message: Message, state: FSMContext) -> None:
    await start_calc(message, state)


@router.callback_query(Menu.filter(F.action == "calc"), StateFilter("*"))
async def cb_calc(cb: CallbackQuery, state: FSMContext) -> None:
    await start_calc(cb, state)


@router.callback_query(Svc.filter(F.action == "calc"), StateFilter("*"))
async def cb_calc_from_service(cb: CallbackQuery, callback_data: Svc, state: FSMContext) -> None:
    if callback_data.id not in SERVICES_BY_ID:
        await ack(cb, "Эта услуга больше недоступна.", alert=True)
        return
    await start_calc(cb, state, callback_data.id)


# ---------------------------------------------------------------- шаги
@router.callback_query(CalcStates.service, Calc.filter(F.action == "svc"))
async def cb_pick_service(cb: CallbackQuery, callback_data: Calc, state: FSMContext) -> None:
    if callback_data.value not in SERVICES_BY_ID:
        await ack(cb, STALE, alert=True)
        return
    await state.update_data(service_id=callback_data.value, addons=[])
    await state.set_state(CalcStates.car_class)
    await _ask_class(cb, callback_data.value)


@router.callback_query(CalcStates.car_class, Calc.filter(F.action == "cls"))
async def cb_pick_class(cb: CallbackQuery, callback_data: Calc, state: FSMContext) -> None:
    data = await state.get_data()
    service = SERVICES_BY_ID.get(data.get("service_id", ""))
    if service is None or callback_data.value not in CAR_CLASSES_BY_ID:
        await ack(cb, STALE, alert=True)
        return
    await state.update_data(car_class=callback_data.value, addons=[])
    if service.addon_ids:
        await state.set_state(CalcStates.addons)
        await _ask_addons(cb, service.id, set())
    else:
        await _show_result(cb, state)


@router.callback_query(CalcStates.addons, Calc.filter(F.action == "addon"))
async def cb_toggle_addon(cb: CallbackQuery, callback_data: Calc, state: FSMContext) -> None:
    data = await state.get_data()
    service = SERVICES_BY_ID.get(data.get("service_id", ""))
    if service is None or callback_data.value not in service.addon_ids:
        await ack(cb, STALE, alert=True)
        return
    selected = set(data.get("addons") or [])
    selected ^= {callback_data.value}
    await state.update_data(addons=sorted(selected))
    await _ask_addons(cb, service.id, selected)


@router.callback_query(CalcStates.addons, Calc.filter(F.action == "done"))
async def cb_done(cb: CallbackQuery, state: FSMContext) -> None:
    await _show_result(cb, state)


@router.callback_query(CalcStates.addons, Calc.filter(F.action == "back"))
async def cb_back_to_class(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("service_id") not in SERVICES_BY_ID:
        await ack(cb, STALE, alert=True)
        return
    await state.set_state(CalcStates.car_class)
    await _ask_class(cb, data["service_id"])


@router.callback_query(Calc.filter(F.action == "restart"), StateFilter("*"))
async def cb_restart(cb: CallbackQuery, state: FSMContext) -> None:
    await start_calc(cb, state)


@router.callback_query(CalcStates.result, Calc.filter(F.action == "book"))
async def cb_book_from_calc(cb: CallbackQuery, state: FSMContext, settings: Settings, repo: Repository) -> None:
    data = await state.get_data()
    try:
        est = calculate_estimate(data["service_id"], data["car_class"], data.get("addons") or [])
    except (KeyError, PricingError):
        await ack(cb, STALE, alert=True)
        return
    await start_booking(cb, state, settings, repo, service_id=est.service_id,
                        car_class_id=est.car_class_id, addon_ids=[a for a in data.get("addons") or []])


# Любое нажатие кнопки калькулятора вне нужного шага — «устарело», а не молчание.
@router.callback_query(Calc.filter())
async def cb_stale(cb: CallbackQuery) -> None:
    await ack(cb, STALE, alert=True)
