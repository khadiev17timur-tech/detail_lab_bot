"""«Мой автомобиль»: сохранённые авто, подтверждённая история, рекомендации по уходу."""
from __future__ import annotations

import logging
from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup as Markup, Message

from config import Settings
from content.care import DISCLAIMER
from content.catalog import CAR_CLASSES, CAR_CLASSES_BY_ID, SERVICES_BY_ID
from database.repository import Repository
from keyboards import client as ckb
from keyboards.callbacks import Cab, CarCb
from services import availability
from services.care import recommendations
from services.formatting import esc, format_when
from services.validators import ValidationError, format_date_ru, validate_car
from states.client import CarStates
from utils.flow import reset_flow
from utils.telegram import Event, ack, respond

log = logging.getLogger(__name__)
router = Router(name="cars")

INTRO = ("🚗 <b>Мой автомобиль</b>\n\nСохраните автомобиль — здесь будет подтверждённая история обслуживания "
         "и подсказки, когда пора вернуться на уход.")


async def show_cars(event: Event, repo: Repository) -> None:
    cars = await repo.list_cars(event.from_user.id)
    text = INTRO if not cars else "🚗 <b>Мой автомобиль</b>\nВыберите автомобиль:"
    if not cars:
        text += "\n\nПока ничего не сохранено. Автомобиль добавляется сам при первой записи, или добавьте его вручную."
    await respond(event, text, ckb.cars_kb(cars))


@router.callback_query(Cab.filter(F.a == "cars"), StateFilter("*"))
async def cb_cars(cb: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    await reset_flow(cb, state)
    await show_cars(cb, repo)


@router.message(Command("car"), StateFilter("*"))
async def cmd_car(message: Message, state: FSMContext, repo: Repository) -> None:
    await reset_flow(message, state)
    await show_cars(message, repo)


def _car_text(car, done, active, settings: Settings, today) -> str:
    cls = CAR_CLASSES_BY_ID.get(car.car_class_id or "")
    lines = [f"🚗 <b>{esc(car.title)}</b>" + (f" · {esc(cls.title.lower())}" if cls else "")]
    lines += ["", "<b>История обслуживания</b> <i>(подтверждена студией)</i>"]
    if done:
        for b in done[:8]:
            lines.append(f"• {esc(format_date_ru(b.start_at[:10] if b.start_at else b.visit_date))} — {esc(b.service_title)}")
        if len(done) > 8:
            lines.append(f"… и ещё {len(done) - 8}")
    else:
        lines.append("Пока нет завершённых визитов.")
    if active:
        lines += ["", "<b>Предстоит</b>"] + [f"• {esc(b.service_title)} — {esc(format_when(b))}" for b in active]
    recs = recommendations(done, active, today)[:3]
    if recs:
        lines += ["", "<b>Что рекомендует студия</b>"]
        for r in recs:
            svc = SERVICES_BY_ID.get(r.rule.recommend_service)
            when = "самое время" if r.overdue else f"с {format_date_ru(r.due)}"
            lines.append(f"• {esc(r.rule.title)}" + (f" ({esc(svc.short_title)})" if svc else "") + f" — {when}")
        lines += ["", f"<i>{esc(DISCLAIMER)}</i>"]
    return "\n".join(lines)


async def _show_car(cb: CallbackQuery, repo: Repository, settings: Settings, car_id: int) -> None:
    car = await repo.get_car(car_id, cb.from_user.id)
    if car is None:
        await ack(cb, "Автомобиль не найден.", alert=True)
        return
    now = availability.local_now(settings.studio)
    done = await repo.done_for_user(cb.from_user.id, car.id)
    active = [b for b in await repo.upcoming_for_user(cb.from_user.id, now) if b.car_id == car.id]
    await respond(cb, _car_text(car, done, active, settings, now.date()), ckb.car_card_kb(car.id))


@router.callback_query(CarCb.filter(F.a == "view"), StateFilter("*"))
async def cb_view(cb: CallbackQuery, callback_data: CarCb, state: FSMContext, repo: Repository, settings: Settings) -> None:
    await reset_flow(cb, state)
    await _show_car(cb, repo, settings, callback_data.id)


@router.callback_query(CarCb.filter(F.a == "del"), StateFilter("*"))
async def cb_del(cb: CallbackQuery, callback_data: CarCb, repo: Repository) -> None:
    car = await repo.get_car(callback_data.id, cb.from_user.id)
    if car is None:
        await ack(cb, "Автомобиль не найден.", alert=True)
        return
    await respond(cb, f"Убрать «{esc(car.title)}» из списка? История визитов в студии сохранится.",
                  ckb.car_delete_confirm_kb(car.id))


@router.callback_query(CarCb.filter(F.a == "del_yes"), StateFilter("*"))
async def cb_del_yes(cb: CallbackQuery, callback_data: CarCb, repo: Repository) -> None:
    await repo.archive_car(callback_data.id, cb.from_user.id)
    await ack(cb, "Убрано")
    await show_cars(cb, repo)


# ----------------------------------------------------------- добавление авто
@router.callback_query(CarCb.filter(F.a == "add"), StateFilter("*"))
async def cb_add(cb: CallbackQuery, state: FSMContext) -> None:
    await reset_flow(cb, state)
    await state.set_state(CarStates.title)
    await respond(cb, "Напишите марку и модель, например: <i>BMW X5</i>.",
                  Markup(inline_keyboard=[[Btn(text="⬅️ Отмена", callback_data=Cab(a="cars").pack())]]))


@router.message(CarStates.title, F.text, ~F.text.startswith("/"))
async def msg_title(message: Message, state: FSMContext) -> None:
    try:
        title = validate_car(message.text)
    except ValidationError as exc:
        await message.answer(f"⚠️ {exc} Попробуйте ещё раз.")
        return
    await state.update_data(title=title)
    await state.set_state(CarStates.car_class)
    rows = [[Btn(text=f"{c.title} — {c.examples}", callback_data=f"carcls:{c.id}")] for c in CAR_CLASSES]
    await message.answer("Выберите класс автомобиля:", reply_markup=Markup(inline_keyboard=rows))


@router.callback_query(CarStates.car_class, F.data.startswith("carcls:"))
async def cb_class(cb: CallbackQuery, state: FSMContext, repo: Repository, settings: Settings) -> None:
    class_id = cb.data.split(":", 1)[1]
    data = await state.get_data()
    if class_id not in CAR_CLASSES_BY_ID or not data.get("title"):
        await ack(cb, "Кнопка устарела. Добавьте автомобиль заново.", alert=True)
        return
    car = await repo.add_car(cb.from_user.id, data["title"], class_id)
    await state.clear()
    await ack(cb, "Сохранено")
    if car:
        await _show_car(cb, repo, settings, car.id)


@router.message(StateFilter(CarStates))
async def msg_unexpected(message: Message) -> None:
    await message.answer("Напишите текстом марку и модель, либо выберите кнопку. /cancel — отмена.")
