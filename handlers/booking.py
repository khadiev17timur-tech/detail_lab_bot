"""Пошаговое оформление заявки (FSM)."""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from datetime import date

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, User

from config import Settings
from content.catalog import CAR_CLASSES_BY_ID, SERVICES_BY_ID
from content.policy import POLICY_TEXT
from database.repository import Repository
from keyboards import booking as kb
from keyboards.callbacks import Book, Menu, Svc
from keyboards.main import BTN_BOOK, main_menu
from services.booking import build_new_booking, find_problems, first_problem_step
from services.formatting import client_booking_card, esc
from services.notifications import notify_admins_new_booking
from services.pricing import PricingError, calculate_estimate, format_rub
from services.validators import (ValidationError, format_date_ru, format_phone,
                                 format_time_ru, normalize_phone, parse_visit_date,
                                 parse_visit_time, validate_car, validate_comment, validate_name)
from states.booking import BookingStates
from utils.flow import reset_flow
from utils.telegram import (Event, ack, drop_reply_keyboard, message_of, respond, send,
                            strip_markup)
from utils.timeutils import now_local

log = logging.getLogger(__name__)
router = Router(name="booking")

STEP_STATE = {
    "service": BookingStates.service, "car": BookingStates.car, "car_class": BookingStates.car_class,
    "date": BookingStates.date, "time": BookingStates.time, "name": BookingStates.name,
    "phone": BookingStates.phone, "comment": BookingStates.comment, "confirm": BookingStates.confirm,
}
STATE_STEP = {state.state: step for step, state in STEP_STATE.items()}

_submit_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
STALE = "Эта кнопка устарела. Начните заново: «🗓 Записаться»."
NOT_COMMAND = ~F.text.startswith("/")


# ------------------------------------------------------------------ шаги
def visible_steps(data: dict) -> list[str]:
    steps = []
    if not data.get("preset_service"):
        steps.append("service")
    steps.append("car")
    if not data.get("preset_class"):
        steps.append("car_class")
    steps += ["date", "time", "name", "phone", "comment", "confirm"]
    return steps


def _header(step: str, data: dict) -> str:
    steps = visible_steps(data)
    return f"🗓 <b>Запись на детейлинг</b> · шаг {steps.index(step) + 1} из {len(steps)}"


def _valid_tg_name(user: User | None) -> str | None:
    if user is None:
        return None
    try:
        return validate_name(user.full_name)
    except ValidationError:
        return None


def _estimate(data: dict):
    try:
        return calculate_estimate(data["service_id"], data["car_class"], data.get("addon_ids") or ())
    except (KeyError, PricingError):
        return None


def draft_summary(data: dict) -> str:
    service = SERVICES_BY_ID[data["service_id"]]
    car_class = CAR_CLASSES_BY_ID[data["car_class"]]
    lines = [
        f"🚘 Услуга: <b>{esc(service.title)}</b>",
        f"🚗 Автомобиль: {esc(data['car'])} ({esc(car_class.title.lower())})",
        f"📅 Желаемая дата: {esc(format_date_ru(data['visit_date']))}",
        f"🕘 Желаемое время: {esc(format_time_ru(data['visit_time']))}",
        f"👤 Имя: {esc(data['name'])}",
        f"📞 Телефон: {esc(format_phone(data['phone']))}",
    ]
    est = _estimate(data)
    if est and est.addons:
        lines.append("➕ Доп. работы: " + esc(", ".join(est.addon_titles)))
    if data.get("comment"):
        lines.append(f"💬 Комментарий: {esc(data['comment'])}")
    if est:
        lines.append(f"💰 Предварительно: от {format_rub(est.total)} <i>(оценка, не окончательная цена)</i>")
    return "\n".join(lines)


def render_confirm(data: dict) -> tuple[str, object]:
    text = (
        f"{_header('confirm', data)}\n\n<b>Проверьте заявку</b>\n\n{draft_summary(data)}\n\n"
        "ℹ️ Это заявка, а не подтверждённая запись: менеджер свяжется с вами и согласует "
        "дату, время и окончательную стоимость.\n\n"
        "Отметьте согласие на обработку персональных данных и отправьте заявку."
    )
    return text, kb.confirm_step_kb(bool(data.get("consent")))


def render_step(step: str, data: dict, settings: Settings, user: User | None):
    studio = settings.studio
    head = _header(step, data)
    if step == "service":
        return f"{head}\n\nВыберите <b>услугу</b>:", kb.service_step_kb()
    if step == "car":
        svc = SERVICES_BY_ID.get(data.get("service_id", ""))
        top = f"Услуга: <b>{esc(svc.title)}</b>\n\n" if svc else ""
        return (f"{head}\n\n{top}Укажите <b>марку и модель</b> автомобиля.\n"
                "Например: <i>BMW X5</i> или <i>Toyota Camry</i>.",
                kb.text_step_kb(back=visible_steps(data)[0] != "car"))
    if step == "car_class":
        return f"{head}\n\nВыберите <b>класс автомобиля</b> — он влияет на стоимость:", kb.class_step_kb()
    if step == "date":
        now = now_local(studio.timezone)
        slots_left_today = any(t > now.strftime("%H:%M") for t in studio.visit_times)
        return (f"{head}\n\nВыберите <b>желаемую дату</b> или напишите её текстом, например <i>25.10</i> "
                "или <i>завтра</i>.\nЭто пожелание — точную дату согласует менеджер.",
                kb.date_step_kb(now.date(), include_today=slots_left_today))
    if step == "time":
        return (f"{head}\n\nДата: <b>{esc(format_date_ru(data['visit_date']))}</b>\n\n"
                "Выберите <b>желаемое время</b> или напишите его в формате ЧЧ:ММ.\n"
                f"Студия работает: {esc(studio.work_hours)}. Время ориентировочное — окончательно его подтвердит менеджер.",
                kb.time_step_kb(studio.visit_times))
    if step == "name":
        return f"{head}\n\nКак к вам обращаться? Напишите <b>имя</b>.", kb.name_step_kb(_valid_tg_name(user))
    if step == "phone":
        return (f"{head}\n\nОтправьте <b>номер телефона</b> кнопкой «Поделиться контактом» "
                "или напишите вручную, например <i>+7 999 123-45-67</i>.", kb.phone_step_kb())
    if step == "comment":
        return (f"{head}\n\nДобавьте <b>комментарий</b> — пожелания, состояние авто, удобное время для звонка. "
                "Это необязательно: можно пропустить.", kb.comment_step_kb())
    if step == "confirm":
        return render_confirm(data)
    raise ValueError(step)


async def show_step(event: Event, state: FSMContext, settings: Settings, step: str,
                    *, notice: str | None = None, edit: bool = True) -> None:
    """Переводит FSM на шаг и показывает его подсказку."""
    data = await state.get_data()
    await state.set_state(STEP_STATE[step])
    text, markup = render_step(step, data, settings, event.from_user)
    if notice:
        text = f"{notice}\n\n{text}"

    msg = message_of(event)
    chat_id = msg.chat.id if msg else event.from_user.id
    reply_kb_was_active = bool(data.get("reply_kb"))
    bot = event.bot

    if reply_kb_was_active and step != "phone":
        await drop_reply_keyboard(event)

    if step == "phone":
        await strip_markup(bot, chat_id, data.get("prompt_id"))
        sent = await send(event, text, markup)
        if isinstance(event, CallbackQuery):
            await ack(event)
    elif isinstance(event, CallbackQuery) and not reply_kb_was_active and edit:
        sent = await respond(event, text, markup)
    else:
        await strip_markup(bot, chat_id, data.get("prompt_id"))
        sent = await send(event, text, markup)
        if isinstance(event, CallbackQuery):
            await ack(event)

    await state.update_data(prompt_id=sent.message_id, reply_kb=(step == "phone"))


async def start_booking(event: Event, state: FSMContext, settings: Settings, *,
                        service_id: str | None = None, car_class_id: str | None = None,
                        addon_ids: list[str] | tuple[str, ...] = (), edit: bool = True) -> None:
    old = await state.get_data()
    if old.get("reply_kb"):
        await drop_reply_keyboard(event)
    await reset_flow(event, state, notify=False)

    data: dict = {"draft_id": uuid.uuid4().hex, "consent": False, "reply_kb": False,
                  "addon_ids": list(addon_ids)}
    if service_id in SERVICES_BY_ID:
        data.update(service_id=service_id, preset_service=True)
    else:
        data["addon_ids"] = []
    if car_class_id in CAR_CLASSES_BY_ID:
        data.update(car_class=car_class_id, preset_class=True)
    await state.set_data(data)
    await show_step(event, state, settings, "car" if data.get("preset_service") else "service", edit=edit)


async def cancel_booking(event: Event, state: FSMContext) -> None:
    data = await state.get_data()
    msg = message_of(event)
    if msg is not None:
        await strip_markup(event.bot, msg.chat.id, data.get("prompt_id"))
        if isinstance(event, CallbackQuery):
            await strip_markup(event.bot, msg.chat.id, msg.message_id)
    await state.clear()
    if isinstance(event, CallbackQuery):
        await ack(event)
    await send(event, "❌ Оформление заявки отменено. Данные не сохранены.", main_menu())


async def go_back(event: Event, state: FSMContext, settings: Settings) -> None:
    data = await state.get_data()
    current = STATE_STEP.get(await state.get_state() or "")
    steps = visible_steps(data)
    if current not in steps or steps.index(current) == 0:
        await cancel_booking(event, state)
        return
    await show_step(event, state, settings, steps[steps.index(current) - 1])


async def next_step(event: Event, state: FSMContext, settings: Settings, current: str) -> None:
    steps = visible_steps(await state.get_data())
    await show_step(event, state, settings, steps[steps.index(current) + 1])


async def _warn(event: Event, text: str) -> None:
    """Сообщение об ошибке ввода: для кнопки — всплывающее, для текста — отдельное сообщение."""
    if isinstance(event, CallbackQuery):
        await ack(event, text, alert=True)
    else:
        await send(event, f"⚠️ {text}")


# ------------------------------------------------------------------ вход
@router.message(F.text == BTN_BOOK, StateFilter("*"))
@router.message(Command("book"), StateFilter("*"))
async def msg_book(message: Message, state: FSMContext, settings: Settings) -> None:
    await start_booking(message, state, settings)


@router.callback_query(Menu.filter(F.action == "book"), StateFilter("*"))
async def cb_book(cb: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    await start_booking(cb, state, settings)


@router.callback_query(Book.filter(F.action == "new"), StateFilter("*"))
async def cb_book_new_message(cb: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    """«Новая заявка» под уведомлением о статусе: само уведомление не затираем."""
    await start_booking(cb, state, settings, edit=False)


@router.callback_query(Svc.filter(F.action == "book"), StateFilter("*"))
async def cb_book_service(cb: CallbackQuery, callback_data: Svc, state: FSMContext, settings: Settings) -> None:
    if callback_data.id not in SERVICES_BY_ID:
        await ack(cb, "Эта услуга больше недоступна.", alert=True)
        return
    await start_booking(cb, state, settings, service_id=callback_data.id)


# ----------------------------------------------------- отмена и «назад»
@router.callback_query(StateFilter(BookingStates), Book.filter(F.action == "cancel"))
async def cb_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    await cancel_booking(cb, state)


@router.message(StateFilter(BookingStates), F.text == kb.BTN_CANCEL)
async def msg_cancel(message: Message, state: FSMContext) -> None:
    await cancel_booking(message, state)


@router.callback_query(StateFilter(BookingStates), Book.filter(F.action == "back"))
async def cb_back(cb: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    await go_back(cb, state, settings)


@router.message(StateFilter(BookingStates), F.text == kb.BTN_BACK)
async def msg_back(message: Message, state: FSMContext, settings: Settings) -> None:
    await go_back(message, state, settings)


# --------------------------------------------------------------- услуга
@router.callback_query(BookingStates.service, Book.filter(F.action == "svc"))
async def cb_service(cb: CallbackQuery, callback_data: Book, state: FSMContext, settings: Settings) -> None:
    if callback_data.value not in SERVICES_BY_ID:
        await ack(cb, STALE, alert=True)
        return
    await state.update_data(service_id=callback_data.value, addon_ids=[])
    await next_step(cb, state, settings, "service")


# ----------------------------------------------------------- авто и класс
@router.message(BookingStates.car, F.text, NOT_COMMAND)
async def msg_car(message: Message, state: FSMContext, settings: Settings) -> None:
    try:
        car = validate_car(message.text)
    except ValidationError as exc:
        await _warn(message, f"{exc} Попробуйте ещё раз.")
        return
    await state.update_data(car=car)
    await next_step(message, state, settings, "car")


@router.callback_query(BookingStates.car_class, Book.filter(F.action == "cls"))
async def cb_class(cb: CallbackQuery, callback_data: Book, state: FSMContext, settings: Settings) -> None:
    if callback_data.value not in CAR_CLASSES_BY_ID:
        await ack(cb, STALE, alert=True)
        return
    await state.update_data(car_class=callback_data.value)
    await next_step(cb, state, settings, "car_class")


# ------------------------------------------------------------- дата, время
async def _apply_date(event: Event, raw: str, state: FSMContext, settings: Settings) -> None:
    studio = settings.studio
    try:
        chosen = parse_visit_date(raw, today=now_local(studio.timezone).date(),
                                  horizon_days=studio.booking_horizon_days)
    except ValidationError as exc:
        await _warn(event, str(exc))
        return
    await state.update_data(visit_date=chosen.isoformat())
    await next_step(event, state, settings, "date")


@router.callback_query(BookingStates.date, Book.filter(F.action == "date"))
async def cb_date(cb: CallbackQuery, callback_data: Book, state: FSMContext, settings: Settings) -> None:
    await _apply_date(cb, callback_data.value, state, settings)


@router.message(BookingStates.date, F.text, NOT_COMMAND)
async def msg_date(message: Message, state: FSMContext, settings: Settings) -> None:
    await _apply_date(message, message.text, state, settings)


async def _apply_time(event: Event, raw: str, state: FSMContext, settings: Settings) -> None:
    studio = settings.studio
    data = await state.get_data()
    try:
        on_date = date.fromisoformat(data["visit_date"])
        value = parse_visit_time(raw, on_date=on_date, now=now_local(studio.timezone),
                                 opens=studio.open_time, closes=studio.close_time)
    except (KeyError, ValueError) as exc:
        if isinstance(exc, ValidationError):
            await _warn(event, str(exc))
        else:
            await _warn(event, STALE)
        return
    await state.update_data(visit_time=value)
    await next_step(event, state, settings, "time")


@router.callback_query(BookingStates.time, Book.filter(F.action == "time"))
async def cb_time(cb: CallbackQuery, callback_data: Book, state: FSMContext, settings: Settings) -> None:
    await _apply_time(cb, callback_data.value, state, settings)


@router.message(BookingStates.time, F.text, NOT_COMMAND)
async def msg_time(message: Message, state: FSMContext, settings: Settings) -> None:
    await _apply_time(message, message.text, state, settings)


# --------------------------------------------------------------- имя
@router.message(BookingStates.name, F.text, NOT_COMMAND)
async def msg_name(message: Message, state: FSMContext, settings: Settings) -> None:
    try:
        name = validate_name(message.text)
    except ValidationError as exc:
        await _warn(message, f"{exc} Попробуйте ещё раз.")
        return
    await state.update_data(name=name)
    await next_step(message, state, settings, "name")


@router.callback_query(BookingStates.name, Book.filter(F.action == "usename"))
async def cb_use_name(cb: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    name = _valid_tg_name(cb.from_user)
    if name is None:
        await ack(cb, "Не получилось использовать имя из Telegram — напишите его вручную.", alert=True)
        return
    await state.update_data(name=name)
    await next_step(cb, state, settings, "name")


# ----------------------------------------------------------------- телефон
async def _apply_phone(message: Message, raw: str, state: FSMContext, settings: Settings) -> None:
    try:
        phone = normalize_phone(raw)
    except ValidationError as exc:
        await _warn(message, f"{exc} Попробуйте ещё раз.")
        return
    await state.update_data(phone=phone)
    await next_step(message, state, settings, "phone")


@router.message(BookingStates.phone, F.contact)
async def msg_phone_contact(message: Message, state: FSMContext, settings: Settings) -> None:
    contact = message.contact
    if contact.user_id is not None and contact.user_id != message.from_user.id:
        await _warn(message, "Отправьте, пожалуйста, свой контакт — или введите номер вручную.")
        return
    await _apply_phone(message, contact.phone_number, state, settings)


@router.message(BookingStates.phone, F.text, NOT_COMMAND)
async def msg_phone_text(message: Message, state: FSMContext, settings: Settings) -> None:
    await _apply_phone(message, message.text, state, settings)


# -------------------------------------------------------------- комментарий
@router.message(BookingStates.comment, F.text, NOT_COMMAND)
async def msg_comment(message: Message, state: FSMContext, settings: Settings) -> None:
    try:
        comment = validate_comment(message.text)
    except ValidationError as exc:
        await _warn(message, str(exc))
        return
    await state.update_data(comment=comment)
    await next_step(message, state, settings, "comment")


@router.callback_query(BookingStates.comment, Book.filter(F.action == "skip"))
async def cb_skip_comment(cb: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    await state.update_data(comment="")
    await next_step(cb, state, settings, "comment")


# ----------------------------------------------------------- подтверждение
@router.callback_query(BookingStates.confirm, Book.filter(F.action == "consent"))
async def cb_consent(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.update_data(consent=not data.get("consent"))
    text, markup = render_confirm(await state.get_data())
    await respond(cb, text, markup)


@router.callback_query(BookingStates.confirm, Book.filter(F.action == "policy"))
async def cb_policy(cb: CallbackQuery) -> None:
    await respond(cb, POLICY_TEXT, kb.policy_kb())


@router.callback_query(BookingStates.confirm, Book.filter(F.action == "summary"))
async def cb_summary(cb: CallbackQuery, state: FSMContext) -> None:
    text, markup = render_confirm(await state.get_data())
    await respond(cb, text, markup)


@router.callback_query(BookingStates.confirm, Book.filter(F.action == "submit"))
async def cb_submit(cb: CallbackQuery, state: FSMContext, settings: Settings, repo: Repository) -> None:
    user_id = cb.from_user.id
    async with _submit_locks[user_id]:
        # Повторное нажатие, пришедшее одновременно: к этому моменту FSM уже очищен первым.
        if await state.get_state() != BookingStates.confirm.state:
            await ack(cb, "Заявка уже отправлена или отменена.", alert=True)
            return
        data = await state.get_data()
        if not data.get("consent"):
            await ack(cb, "Сначала отметьте согласие на обработку персональных данных.", alert=True)
            return
        problems = find_problems(data)
        if problems:
            step = first_problem_step(problems) or "service"
            await show_step(cb, state, settings, step,
                            notice="⚠️ В заявке не хватает данных — давайте заполним этот шаг.")
            return
        try:
            new = build_new_booking(data, user_id=user_id)
            await repo.upsert_user(user_id, cb.from_user.username, cb.from_user.full_name)  # для внешнего ключа
            booking, created = await repo.create_booking(new)
        except ValueError:
            log.exception("Не удалось собрать заявку из черновика")
            await ack(cb, "Не удалось отправить заявку. Проверьте данные и попробуйте ещё раз.", alert=True)
            return
        except Exception:
            log.exception("Ошибка БД при сохранении заявки")
            await ack(cb, "Временная ошибка. Ничего не потеряно — нажмите «Отправить заявку» ещё раз через минуту.",
                      alert=True)
            return
        await state.clear()

    if not created:
        await ack(cb, f"Эта заявка уже отправлена — №{booking.id}.", alert=True)
        await respond(cb, client_booking_card(booking), None)
        return

    await respond(cb, client_booking_card(booking), None)
    await send(cb, "Вернуться в меню можно кнопками ниже 👇", main_menu())
    try:
        await notify_admins_new_booking(cb.bot, settings.admin_ids, booking, settings.studio.timezone)
    except Exception:
        log.exception("Ошибка при уведомлении администраторов о заявке №%s", booking.id)


# -------------------------------------------------- запасные обработчики
@router.message(StateFilter(BookingStates))
async def msg_unexpected(message: Message, state: FSMContext, settings: Settings) -> None:
    """Сообщение не по теме шага (стикер, фото, неизвестная команда): подсказываем, что нужно."""
    step = STATE_STEP.get(await state.get_state() or "")
    if step is None:
        await state.clear()
        await message.answer("Давайте начнём сначала.", reply_markup=main_menu())
        return
    await show_step(message, state, settings, step,
                    notice="⚠️ Не понял сообщение. Используйте кнопки или ответьте текстом. /cancel — отмена.")


@router.callback_query(Book.filter())
async def cb_stale(cb: CallbackQuery) -> None:
    await ack(cb, STALE, alert=True)
