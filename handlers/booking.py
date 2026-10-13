"""Пошаговое оформление заявки (FSM) с выбором свободного времени."""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections import OrderedDict
from datetime import date, datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, User

from config import Settings
from content.catalog import CAR_CLASSES_BY_ID, SERVICES_BY_ID
from content.policy import POLICY_TEXT
from database.errors import SlotTaken, VoucherUnavailable
from database.repository import Repository
from keyboards import booking as kb
from keyboards.callbacks import Book, Menu, Svc
from keyboards.main import BTN_BOOK, main_menu
from services import availability, planning
from services.booking import build_new_booking, find_problems, first_problem_step
from services.discounts import best_voucher
from services.formatting import client_booking_card, esc
from services.notifications import notify_admins_new_booking
from services.pricing import PricingError, calculate_estimate, format_rub
from services.schedule import from_iso, to_iso
from services.validators import (ValidationError, format_date_ru, format_phone, normalize_phone,
                                 parse_visit_date, parse_visit_time, validate_car, validate_comment, validate_name)
from states.booking import BookingStates
from utils.flow import reset_flow
from utils.telegram import (Event, ack, drop_reply_keyboard, message_of, respond, send, strip_markup)

log = logging.getLogger(__name__)
router = Router(name="booking")

STEP_STATE = {
    "service": BookingStates.service, "car": BookingStates.car, "car_class": BookingStates.car_class,
    "date": BookingStates.date, "time": BookingStates.time, "name": BookingStates.name,
    "phone": BookingStates.phone, "comment": BookingStates.comment, "confirm": BookingStates.confirm,
}
STATE_STEP = {state.state: step for step, state in STEP_STATE.items()}

STALE = "Эта кнопка устарела. Начните заново: «🗓 Записаться»."
NOT_COMMAND = ~F.text.startswith("/")


class _LockPool:
    """Замки на пользователя с ограничением размера (иначе словарь рос бы вечно)."""

    def __init__(self, limit: int = 2048):
        self._locks: OrderedDict[int, asyncio.Lock] = OrderedDict()
        self._limit = limit

    def get(self, key: int) -> asyncio.Lock:
        lock = self._locks.pop(key, None) or asyncio.Lock()
        self._locks[key] = lock
        while len(self._locks) > self._limit:
            oldest = next(iter(self._locks))
            if self._locks[oldest].locked():
                break
            self._locks.pop(oldest)
        return lock


_submit_locks = _LockPool()


# ------------------------------------------------------------------ шаги
def visible_steps(data: dict) -> list[str]:
    steps = []
    if not data.get("preset_service"):
        steps.append("service")
    steps.append("car")
    if not (data.get("preset_class") or data.get("class_locked")):
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


async def _voucher_offer(repo: Repository, user_id: int, data: dict):
    """Лучший применимый купон и скидка в ₽ (рассчитывается на сервере при каждом показе и при отправке)."""
    est = _estimate(data)
    if est is None:
        return None
    vouchers = await repo.active_vouchers(user_id, datetime.now(timezone.utc))
    return best_voucher(vouchers, est.total, datetime.now(timezone.utc))


def _when_text(data: dict) -> str:
    start, end = from_iso(data["start_at"]), from_iso(data["end_at"])
    if start.date() == end.date():
        return f"{format_date_ru(start.date())} · {start:%H:%M}–{end:%H:%M}"
    return f"{format_date_ru(start.date())} {start:%H:%M} → {format_date_ru(end.date())} {end:%H:%M}"


def draft_summary(data: dict, discount: int = 0) -> str:
    service = SERVICES_BY_ID[data["service_id"]]
    car_class = CAR_CLASSES_BY_ID[data["car_class"]]
    lines = [
        f"🚘 Услуга: <b>{esc(service.title)}</b>",
        f"🚗 Автомобиль: {esc(data['car'])} ({esc(car_class.title.lower())})",
        f"📅 Время: {esc(_when_text(data))}",
        f"👤 Имя: {esc(data['name'])}",
        f"📞 Телефон: {esc(format_phone(data['phone']))}",
    ]
    est = _estimate(data)
    if est and est.addons:
        lines.append("➕ Доп. работы: " + esc(", ".join(est.addon_titles)))
    if data.get("comment"):
        lines.append(f"💬 Комментарий: {esc(data['comment'])}")
    if est:
        if discount:
            lines.append(f"💰 Предварительно: от {format_rub(max(0, est.total - discount))} "
                         f"<i>(с учётом скидки {format_rub(discount)}; оценка, не окончательная цена)</i>")
        else:
            lines.append(f"💰 Предварительно: от {format_rub(est.total)} <i>(оценка, не окончательная цена)</i>")
    return "\n".join(lines)


async def render_confirm(data: dict, repo: Repository, user_id: int) -> tuple[str, object]:
    offer = await _voucher_offer(repo, user_id, data)
    applied = bool(offer) and data.get("use_voucher", True)
    discount = offer[1] if applied else 0
    text = (
        f"{_header('confirm', data)}\n\n<b>Проверьте заявку</b>\n\n{draft_summary(data, discount)}\n\n"
        "ℹ️ Время предварительно резервируется за вами. Менеджер подтвердит запись и согласует "
        "окончательную стоимость.\n\n"
        "Отметьте согласие на обработку персональных данных и отправьте заявку."
    )
    markup = kb.confirm_step_kb(bool(data.get("consent")), bool(data.get("marketing")),
                                (offer[1], applied) if offer else None)
    return text, markup


async def render_step(step: str, data: dict, settings: Settings, repo: Repository, user: User | None):
    studio = settings.studio
    head = _header(step, data)
    if step == "service":
        return f"{head}\n\nВыберите <b>услугу</b>:", kb.service_step_kb()
    if step == "car":
        svc = SERVICES_BY_ID.get(data.get("service_id", ""))
        top = f"Услуга: <b>{esc(svc.title)}</b>\n\n" if svc else ""
        cars = await repo.list_cars(user.id) if user else []
        hint = "Выберите сохранённый автомобиль или напишите " if cars else "Укажите "
        return (f"{head}\n\n{top}{hint}<b>марку и модель</b> автомобиля.\n"
                "Например: <i>BMW X5</i> или <i>Toyota Camry</i>.",
                kb.car_step_kb(cars, back=visible_steps(data)[0] != "car"))
    if step == "car_class":
        return f"{head}\n\nВыберите <b>класс автомобиля</b> — он влияет на стоимость:", kb.class_step_kb()
    if step == "date":
        service = SERVICES_BY_ID[data["service_id"]]
        now = availability.local_now(studio)
        days = await availability.dates_with_slots(repo, studio, service, now)
        if not days:
            return (f"{head}\n\n😔 В ближайшее время свободного времени для этой услуги нет. "
                    "Свяжитесь с менеджером — подберём вариант.", kb.text_step_kb())
        return (f"{head}\n\nВыберите <b>дату</b> — показаны только дни со свободным временем. "
                "Другую дату можно написать текстом, например <i>25.10</i>.",
                kb.date_step_kb(days, now.date()))
    if step == "time":
        service = SERVICES_BY_ID[data["service_id"]]
        day = date.fromisoformat(data["visit_date"])
        slots = await availability.slots_for_day(repo, studio, service, day, availability.local_now(studio))
        note = ("Услуга занимает несколько дней: автомобиль принимаем на открытии." if service.days > 1
                else f"Услуга займёт ориентировочно {esc(service.duration)}.")
        if not slots:
            return (f"{head}\n\nДата: <b>{esc(format_date_ru(day))}</b>\n\nК сожалению, свободное время на эту дату "
                    "уже закончилось. Вернитесь назад и выберите другой день.", kb.text_step_kb())
        return (f"{head}\n\nДата: <b>{esc(format_date_ru(day))}</b>\n{note}\n\n"
                "Выберите <b>время начала</b> — показано только свободное:", kb.time_step_kb(slots))
    if step == "name":
        return f"{head}\n\nКак к вам обращаться? Напишите <b>имя</b>.", kb.name_step_kb(_valid_tg_name(user))
    if step == "phone":
        return (f"{head}\n\nОтправьте <b>номер телефона</b> кнопкой «Поделиться контактом» "
                "или напишите вручную, например <i>+7 999 123-45-67</i>.", kb.phone_step_kb())
    if step == "comment":
        return (f"{head}\n\nДобавьте <b>комментарий</b> — пожелания, состояние авто, удобное время для звонка. "
                "Это необязательно: можно пропустить.", kb.comment_step_kb())
    if step == "confirm":
        return await render_confirm(data, repo, user.id)
    raise ValueError(step)


async def show_step(event: Event, state: FSMContext, settings: Settings, repo: Repository, step: str,
                    *, notice: str | None = None, edit: bool = True) -> None:
    """Переводит FSM на шаг и показывает его подсказку."""
    data = await state.get_data()
    await state.set_state(STEP_STATE[step])
    text, markup = await render_step(step, data, settings, repo, event.from_user)
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


async def start_booking(event: Event, state: FSMContext, settings: Settings, repo: Repository, *,
                        service_id: str | None = None, car_class_id: str | None = None,
                        addon_ids: list[str] | tuple[str, ...] = (), edit: bool = True) -> None:
    old = await state.get_data()
    if old.get("reply_kb"):
        await drop_reply_keyboard(event)
    await reset_flow(event, state, notify=False)

    user = await repo.get_user(event.from_user.id)
    data: dict = {"draft_id": uuid.uuid4().hex, "consent": False, "reply_kb": False,
                  "addon_ids": list(addon_ids), "marketing": bool(user and user.marketing_consent)}
    if service_id in SERVICES_BY_ID:
        data.update(service_id=service_id, preset_service=True)
    else:
        data["addon_ids"] = []
    if car_class_id in CAR_CLASSES_BY_ID:
        data.update(car_class=car_class_id, preset_class=True)
    await state.set_data(data)
    await show_step(event, state, settings, repo, "car" if data.get("preset_service") else "service", edit=edit)


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


async def go_back(event: Event, state: FSMContext, settings: Settings, repo: Repository) -> None:
    data = await state.get_data()
    current = STATE_STEP.get(await state.get_state() or "")
    steps = visible_steps(data)
    if current not in steps or steps.index(current) == 0:
        await cancel_booking(event, state)
        return
    await show_step(event, state, settings, repo, steps[steps.index(current) - 1])


async def next_step(event: Event, state: FSMContext, settings: Settings, repo: Repository, current: str) -> None:
    steps = visible_steps(await state.get_data())
    await show_step(event, state, settings, repo, steps[steps.index(current) + 1])


async def _warn(event: Event, text: str) -> None:
    """Сообщение об ошибке ввода: для кнопки — всплывающее, для текста — отдельное сообщение."""
    if isinstance(event, CallbackQuery):
        await ack(event, text, alert=True)
    else:
        await send(event, f"⚠️ {text}")


# ------------------------------------------------------------------ вход
@router.message(F.text == BTN_BOOK, StateFilter("*"))
@router.message(Command("book"), StateFilter("*"))
async def msg_book(message: Message, state: FSMContext, settings: Settings, repo: Repository) -> None:
    await start_booking(message, state, settings, repo)


@router.callback_query(Menu.filter(F.action == "book"), StateFilter("*"))
async def cb_book(cb: CallbackQuery, state: FSMContext, settings: Settings, repo: Repository) -> None:
    await start_booking(cb, state, settings, repo)


@router.callback_query(Book.filter(F.action == "new"), StateFilter("*"))
async def cb_book_new_message(cb: CallbackQuery, state: FSMContext, settings: Settings, repo: Repository) -> None:
    """«Новая заявка» под уведомлением о статусе: само уведомление не затираем."""
    await start_booking(cb, state, settings, repo, edit=False)


@router.callback_query(Svc.filter(F.action == "book"), StateFilter("*"))
async def cb_book_service(cb: CallbackQuery, callback_data: Svc, state: FSMContext, settings: Settings,
                          repo: Repository) -> None:
    if callback_data.id not in SERVICES_BY_ID:
        await ack(cb, "Эта услуга больше недоступна.", alert=True)
        return
    await start_booking(cb, state, settings, repo, service_id=callback_data.id)


# ----------------------------------------------------- отмена и «назад»
@router.callback_query(StateFilter(BookingStates), Book.filter(F.action == "cancel"))
async def cb_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    await cancel_booking(cb, state)


@router.message(StateFilter(BookingStates), F.text == kb.BTN_CANCEL)
async def msg_cancel(message: Message, state: FSMContext) -> None:
    await cancel_booking(message, state)


@router.callback_query(StateFilter(BookingStates), Book.filter(F.action == "back"))
async def cb_back(cb: CallbackQuery, state: FSMContext, settings: Settings, repo: Repository) -> None:
    await go_back(cb, state, settings, repo)


@router.message(StateFilter(BookingStates), F.text == kb.BTN_BACK)
async def msg_back(message: Message, state: FSMContext, settings: Settings, repo: Repository) -> None:
    await go_back(message, state, settings, repo)


# --------------------------------------------------------------- услуга
@router.callback_query(BookingStates.service, Book.filter(F.action == "svc"))
async def cb_service(cb: CallbackQuery, callback_data: Book, state: FSMContext, settings: Settings,
                     repo: Repository) -> None:
    if callback_data.value not in SERVICES_BY_ID:
        await ack(cb, STALE, alert=True)
        return
    await state.update_data(service_id=callback_data.value, addon_ids=[], visit_date=None, start_at=None, end_at=None)
    await next_step(cb, state, settings, repo, "service")


# ----------------------------------------------------------- авто и класс
@router.message(BookingStates.car, F.text, NOT_COMMAND)
async def msg_car(message: Message, state: FSMContext, settings: Settings, repo: Repository) -> None:
    try:
        car = validate_car(message.text)
    except ValidationError as exc:
        await _warn(message, f"{exc} Попробуйте ещё раз.")
        return
    await state.update_data(car=car, class_locked=False)
    await next_step(message, state, settings, repo, "car")


@router.callback_query(BookingStates.car, Book.filter(F.action == "car"))
async def cb_saved_car(cb: CallbackQuery, callback_data: Book, state: FSMContext, settings: Settings,
                       repo: Repository) -> None:
    try:
        car_id = int(callback_data.value)
    except ValueError:
        car_id = 0
    car = await repo.get_car(car_id, cb.from_user.id)          # только свои автомобили
    if car is None:
        await ack(cb, "Автомобиль не найден. Напишите марку и модель текстом.", alert=True)
        return
    update = {"car": car.title, "class_locked": False}
    if car.car_class_id in CAR_CLASSES_BY_ID:
        update.update(car_class=car.car_class_id, class_locked=True)
    await state.update_data(**update)
    await next_step(cb, state, settings, repo, "car")


@router.callback_query(BookingStates.car_class, Book.filter(F.action == "cls"))
async def cb_class(cb: CallbackQuery, callback_data: Book, state: FSMContext, settings: Settings,
                   repo: Repository) -> None:
    if callback_data.value not in CAR_CLASSES_BY_ID:
        await ack(cb, STALE, alert=True)
        return
    await state.update_data(car_class=callback_data.value)
    await next_step(cb, state, settings, repo, "car_class")


# ------------------------------------------------------------- дата, время
async def _apply_date(event: Event, raw: str, state: FSMContext, settings: Settings, repo: Repository) -> None:
    studio = settings.studio
    data = await state.get_data()
    service = SERVICES_BY_ID.get(data.get("service_id", ""))
    if service is None:
        await _warn(event, STALE)
        return
    now = availability.local_now(studio)
    try:
        chosen = parse_visit_date(raw, today=now.date(), horizon_days=studio.booking_horizon_days)
    except ValidationError as exc:
        await _warn(event, str(exc))
        return
    if not await availability.slots_for_day(repo, studio, service, chosen, now):
        await _warn(event, "На эту дату нет свободного времени. Выберите другую дату из списка.")
        return
    await state.update_data(visit_date=chosen.isoformat(), start_at=None, end_at=None, visit_time=None)
    await next_step(event, state, settings, repo, "date")


@router.callback_query(BookingStates.date, Book.filter(F.action == "date"))
async def cb_date(cb: CallbackQuery, callback_data: Book, state: FSMContext, settings: Settings,
                  repo: Repository) -> None:
    await _apply_date(cb, callback_data.value, state, settings, repo)


@router.message(BookingStates.date, F.text, NOT_COMMAND)
async def msg_date(message: Message, state: FSMContext, settings: Settings, repo: Repository) -> None:
    await _apply_date(message, message.text, state, settings, repo)


async def _apply_time(event: Event, raw: str, state: FSMContext, settings: Settings, repo: Repository) -> None:
    studio = settings.studio
    data = await state.get_data()
    service = SERVICES_BY_ID.get(data.get("service_id", ""))
    try:
        day = date.fromisoformat(data["visit_date"])
        hhmm = parse_visit_time(raw, on_date=day, now=availability.local_now(studio),
                                opens=studio.open_time, closes=studio.close_time)
        start = datetime.combine(day, datetime.strptime(hhmm, "%H:%M").time())
    except ValidationError as exc:
        await _warn(event, str(exc))
        return
    except (KeyError, TypeError, ValueError):
        await _warn(event, STALE)
        return
    if service is None or start not in await availability.slots_for_day(
            repo, studio, service, day, availability.local_now(studio)):
        await _warn(event, "Это время недоступно (занято или не помещается в расписание). Выберите время из списка.")
        return
    s, e = availability.interval_of(service, start, studio)
    await state.update_data(visit_time=hhmm, start_at=to_iso(s), end_at=to_iso(e))
    await next_step(event, state, settings, repo, "time")


@router.callback_query(BookingStates.time, Book.filter(F.action == "time"))
async def cb_time(cb: CallbackQuery, callback_data: Book, state: FSMContext, settings: Settings,
                  repo: Repository) -> None:
    v = callback_data.value
    await _apply_time(cb, f"{v[:2]}:{v[2:]}" if len(v) == 4 and v.isdigit() else v, state, settings, repo)


@router.message(BookingStates.time, F.text, NOT_COMMAND)
async def msg_time(message: Message, state: FSMContext, settings: Settings, repo: Repository) -> None:
    await _apply_time(message, message.text, state, settings, repo)


# --------------------------------------------------------------- имя
@router.message(BookingStates.name, F.text, NOT_COMMAND)
async def msg_name(message: Message, state: FSMContext, settings: Settings, repo: Repository) -> None:
    try:
        name = validate_name(message.text)
    except ValidationError as exc:
        await _warn(message, f"{exc} Попробуйте ещё раз.")
        return
    await state.update_data(name=name)
    await next_step(message, state, settings, repo, "name")


@router.callback_query(BookingStates.name, Book.filter(F.action == "usename"))
async def cb_use_name(cb: CallbackQuery, state: FSMContext, settings: Settings, repo: Repository) -> None:
    name = _valid_tg_name(cb.from_user)
    if name is None:
        await ack(cb, "Не получилось использовать имя из Telegram — напишите его вручную.", alert=True)
        return
    await state.update_data(name=name)
    await next_step(cb, state, settings, repo, "name")


# ----------------------------------------------------------------- телефон
async def _apply_phone(message: Message, raw: str, state: FSMContext, settings: Settings, repo: Repository) -> None:
    try:
        phone = normalize_phone(raw)
    except ValidationError as exc:
        await _warn(message, f"{exc} Попробуйте ещё раз.")
        return
    await state.update_data(phone=phone)
    await next_step(message, state, settings, repo, "phone")


@router.message(BookingStates.phone, F.contact)
async def msg_phone_contact(message: Message, state: FSMContext, settings: Settings, repo: Repository) -> None:
    contact = message.contact
    if contact.user_id is not None and contact.user_id != message.from_user.id:
        await _warn(message, "Отправьте, пожалуйста, свой контакт — или введите номер вручную.")
        return
    await _apply_phone(message, contact.phone_number, state, settings, repo)


@router.message(BookingStates.phone, F.text, NOT_COMMAND)
async def msg_phone_text(message: Message, state: FSMContext, settings: Settings, repo: Repository) -> None:
    await _apply_phone(message, message.text, state, settings, repo)


# -------------------------------------------------------------- комментарий
@router.message(BookingStates.comment, F.text, NOT_COMMAND)
async def msg_comment(message: Message, state: FSMContext, settings: Settings, repo: Repository) -> None:
    try:
        comment = validate_comment(message.text)
    except ValidationError as exc:
        await _warn(message, str(exc))
        return
    if comment.lower() in {"-", "—", "нет", "не нужно", "пропустить"}:
        comment = ""                                    # «нет комментария» не должен попадать в заявку
    await state.update_data(comment=comment)
    await next_step(message, state, settings, repo, "comment")


@router.callback_query(BookingStates.comment, Book.filter(F.action == "skip"))
async def cb_skip_comment(cb: CallbackQuery, state: FSMContext, settings: Settings, repo: Repository) -> None:
    await state.update_data(comment="")
    await next_step(cb, state, settings, repo, "comment")


# ----------------------------------------------------------- подтверждение
async def _refresh_confirm(cb: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    text, markup = await render_confirm(await state.get_data(), repo, cb.from_user.id)
    await respond(cb, text, markup)


@router.callback_query(BookingStates.confirm, Book.filter(F.action == "consent"))
async def cb_consent(cb: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    data = await state.get_data()
    await state.update_data(consent=not data.get("consent"))
    await _refresh_confirm(cb, state, repo)


@router.callback_query(BookingStates.confirm, Book.filter(F.action == "marketing"))
async def cb_marketing(cb: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    data = await state.get_data()
    await state.update_data(marketing=not data.get("marketing"))
    await _refresh_confirm(cb, state, repo)


@router.callback_query(BookingStates.confirm, Book.filter(F.action == "voucher"))
async def cb_voucher(cb: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    data = await state.get_data()
    await state.update_data(use_voucher=not data.get("use_voucher", True))
    await _refresh_confirm(cb, state, repo)


@router.callback_query(BookingStates.confirm, Book.filter(F.action == "policy"))
async def cb_policy(cb: CallbackQuery) -> None:
    await respond(cb, POLICY_TEXT, kb.policy_kb())


@router.callback_query(BookingStates.confirm, Book.filter(F.action == "summary"))
async def cb_summary(cb: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    await _refresh_confirm(cb, state, repo)


@router.callback_query(BookingStates.confirm, Book.filter(F.action == "submit"))
async def cb_submit(cb: CallbackQuery, state: FSMContext, settings: Settings, repo: Repository) -> None:
    user_id = cb.from_user.id
    studio = settings.studio
    async with _submit_locks.get(user_id):
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
            await show_step(cb, state, settings, repo, step,
                            notice="⚠️ В заявке не хватает данных — давайте заполним этот шаг.")
            return
        offer = await _voucher_offer(repo, user_id, data)
        voucher, discount = (offer if offer and data.get("use_voucher", True) else (None, 0))
        start = from_iso(data["start_at"])
        try:
            new = build_new_booking(data, user_id=user_id,
                                    voucher_id=voucher.id if voucher else None, discount_rub=discount)
            jobs = planning.reminder_jobs(start, datetime.now(timezone.utc), studio)
            booking, created = await repo.create_booking(new, posts=studio.posts, jobs=jobs)
        except SlotTaken:
            await state.update_data(start_at=None, end_at=None, visit_time=None)
            await show_step(cb, state, settings, repo, "time",
                            notice="😔 Это время только что заняли. Выберите другое — свободное показано ниже.")
            return
        except VoucherUnavailable:
            await state.update_data(use_voucher=False)
            await show_step(cb, state, settings, repo, "confirm",
                            notice="ℹ️ Скидка стала недоступна (использована или истёк срок). Проверьте заявку.")
            return
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

    if created and data.get("marketing"):
        await repo.set_marketing(user_id, True)
    if not created:
        await ack(cb, f"Эта заявка уже отправлена — №{booking.id}.", alert=True)
        await respond(cb, client_booking_card(booking), None)
        return

    await respond(cb, client_booking_card(booking), None)
    await send(cb, "Вернуться в меню можно кнопками ниже 👇", main_menu())
    try:
        await notify_admins_new_booking(cb.bot, settings.admin_ids, booking, studio.timezone)
    except Exception:
        log.exception("Ошибка при уведомлении администраторов о заявке №%s", booking.id)


# -------------------------------------------------- запасные обработчики
@router.message(StateFilter(BookingStates))
async def msg_unexpected(message: Message, state: FSMContext, settings: Settings, repo: Repository) -> None:
    """Сообщение не по теме шага (стикер, фото, неизвестная команда): подсказываем, что нужно."""
    step = STATE_STEP.get(await state.get_state() or "")
    if step is None:
        await state.clear()
        await message.answer("Давайте начнём сначала.", reply_markup=main_menu())
        return
    await show_step(message, state, settings, repo, step,
                    notice="⚠️ Не понял сообщение. Используйте кнопки или ответьте текстом. /cancel — отмена.")


@router.callback_query(Book.filter())
async def cb_stale(cb: CallbackQuery) -> None:
    await ack(cb, STALE, alert=True)
