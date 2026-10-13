"""Личный кабинет: мои записи (просмотр, подтверждение, отмена, перенос), скидки, уведомления."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import Settings
from content.catalog import SERVICES_BY_ID
from database.errors import NotAllowed, SlotTaken
from database.models import Booking, BookingStatus
from database.repository import Repository
from keyboards import client as ckb
from keyboards.admin import booking_actions_kb
from keyboards.callbacks import Cab, Menu, Mine
from keyboards.main import BTN_CABINET
from services import availability, planning
from services.discounts import voucher_discount
from services.formatting import esc, format_when, my_booking_card
from services.notifications import notify_admins_text
from services.schedule import from_iso
from services.validators import format_date_ru
from utils.flow import reset_flow
from utils.telegram import Event, ack, respond

log = logging.getLogger(__name__)
router = Router(name="cabinet")

CABINET_TEXT = ("👤 <b>Мой кабинет</b>\n\nЗдесь ваши записи, автомобили и персональные предложения. "
                "Записи можно переносить и отменять самостоятельно.")


async def show_cabinet(event: Event) -> None:
    await respond(event, CABINET_TEXT, ckb.cabinet_kb())


@router.message(F.text == BTN_CABINET, StateFilter("*"))
@router.message(Command("cabinet"), StateFilter("*"))
async def msg_cabinet(message: Message, state: FSMContext) -> None:
    await reset_flow(message, state)
    await show_cabinet(message)


@router.callback_query(Cab.filter(F.a == "home"), StateFilter("*"))
@router.callback_query(Menu.filter(F.action == "cabinet"), StateFilter("*"))
async def cb_cabinet(cb: CallbackQuery, state: FSMContext) -> None:
    await reset_flow(cb, state)
    await show_cabinet(cb)


# ------------------------------------------------------------------ записи
async def _own(repo: Repository, booking_id: int, user_id: int) -> Booking | None:
    b = await repo.get_booking(booking_id)
    return b if b is not None and b.user_id == user_id else None      # чужие записи недоступны


async def show_my_bookings(event: Event, repo: Repository, settings: Settings) -> None:
    now = availability.local_now(settings.studio)
    items = await repo.upcoming_for_user(event.from_user.id, now)
    if not items:
        await respond(event, "📅 <b>Мои записи</b>\n\nАктивных записей пока нет. Хотите записаться?",
                      ckb.my_bookings_kb([]))
        return
    await respond(event, f"📅 <b>Мои записи</b>\nПредстоящих: {len(items)}. Выберите запись:", ckb.my_bookings_kb(items))


@router.callback_query(Cab.filter(F.a == "bookings"), StateFilter("*"))
async def cb_bookings(cb: CallbackQuery, state: FSMContext, repo: Repository, settings: Settings) -> None:
    await reset_flow(cb, state)
    await show_my_bookings(cb, repo, settings)


async def _show_booking(cb: CallbackQuery, repo: Repository, settings: Settings, booking_id: int) -> None:
    b = await _own(repo, booking_id, cb.from_user.id)
    if b is None:
        await ack(cb, "Запись не найдена.", alert=True)
        return
    await respond(cb, my_booking_card(b, settings.studio), ckb.booking_card_kb(b))


@router.callback_query(Mine.filter(F.a == "view"), StateFilter("*"))
async def cb_view(cb: CallbackQuery, callback_data: Mine, state: FSMContext, repo: Repository, settings: Settings) -> None:
    await reset_flow(cb, state)
    await _show_booking(cb, repo, settings, callback_data.id)


@router.callback_query(Mine.filter(F.a == "ok"), StateFilter("*"))
async def cb_confirm_attendance(cb: CallbackQuery, callback_data: Mine, repo: Repository, settings: Settings) -> None:
    b = await _own(repo, callback_data.id, cb.from_user.id)
    if b is None or not b.is_active:
        await ack(cb, "Эта запись уже неактуальна.", alert=True)
        return
    first = await repo.confirm_attendance(b.id, cb.from_user.id)
    await ack(cb, "Спасибо, ждём вас! ✅" if first else "Вы уже подтверждали визит.")
    if first:
        refreshed = await repo.get_booking(b.id)
        await respond(cb, my_booking_card(refreshed, settings.studio), ckb.booking_card_kb(refreshed))


# --------------------------------------------------------------------- отмена
def _is_late(b: Booking, settings: Settings) -> bool:
    if not b.start_at:
        return False
    return from_iso(b.start_at) - availability.local_now(settings.studio) < timedelta(hours=settings.studio.cancel_before_hours)


@router.callback_query(Mine.filter(F.a == "cancel"), StateFilter("*"))
async def cb_cancel(cb: CallbackQuery, callback_data: Mine, repo: Repository, settings: Settings) -> None:
    b = await _own(repo, callback_data.id, cb.from_user.id)
    if b is None or not b.is_active:
        await ack(cb, "Эту запись уже нельзя отменить.", alert=True)
        return
    warn = ""
    if _is_late(b, settings):
        warn = (f"\n\n⚠️ До визита меньше {settings.studio.cancel_before_hours} ч. Пожалуйста, предупредите менеджера — "
                "мы стараемся заполнить освободившееся время.")
    await respond(cb, f"❌ <b>Отменить запись №{b.id}?</b>\n\n{esc(b.service_title)} · {esc(format_when(b))}{warn}",
                  ckb.cancel_confirm_kb(b.id))


@router.callback_query(Mine.filter(F.a == "cancel_yes"), StateFilter("*"))
async def cb_cancel_yes(cb: CallbackQuery, callback_data: Mine, repo: Repository, settings: Settings) -> None:
    b = await _own(repo, callback_data.id, cb.from_user.id)
    if b is None:
        await ack(cb, "Запись не найдена.", alert=True)
        return
    result = await repo.set_status(b.id, BookingStatus.CANCELLED, actor_id=cb.from_user.id, cancelled_by="client")
    if not result.changed:
        await ack(cb, "Запись уже отменена или завершена.", alert=True)
        await _show_booking(cb, repo, settings, b.id)
        return
    await ack(cb)
    await respond(cb, f"Запись №{b.id} отменена. Время освобождено. Будем рады видеть вас снова!",
                  ckb.my_bookings_kb([]))
    late = " (поздняя отмена)" if _is_late(b, settings) else ""
    await notify_admins_text(
        cb.bot, settings.admin_ids,
        f"🚫 <b>Клиент отменил запись №{b.id}</b>{late}\n{esc(b.service_title)} · {esc(format_when(b))}\n"
        f"{esc(b.name)} · {esc(b.phone)}")


# -------------------------------------------------------------------- перенос
async def _service_or_alert(cb: CallbackQuery, b: Booking):
    service = SERVICES_BY_ID.get(b.service_id)
    if service is None:
        await ack(cb, "Для этой услуги перенос недоступен — свяжитесь с менеджером.", alert=True)
    return service


@router.callback_query(Mine.filter(F.a == "resched"), StateFilter("*"))
async def cb_resched(cb: CallbackQuery, callback_data: Mine, repo: Repository, settings: Settings) -> None:
    b = await _own(repo, callback_data.id, cb.from_user.id)
    if b is None or not b.is_active:
        await ack(cb, "Эту запись нельзя перенести.", alert=True)
        return
    service = await _service_or_alert(cb, b)
    if service is None:
        return
    now = availability.local_now(settings.studio)
    own = (from_iso(b.start_at), from_iso(b.end_at)) if b.start_at and b.end_at else None
    days = await availability.dates_with_slots(repo, settings.studio, service, now, ignore=own)
    if not days:
        await respond(cb, "😔 В ближайшее время свободных окон нет. Свяжитесь с менеджером — подберём вариант.",
                      ckb.my_bookings_kb([]))
        return
    await respond(cb, f"🔁 <b>Перенос записи №{b.id}</b>\nСейчас: {esc(format_when(b))}\n\nВыберите новую дату:",
                  ckb.resched_dates_kb(b.id, days, now.date()))


@router.callback_query(Mine.filter(F.a == "rdate"), StateFilter("*"))
async def cb_resched_date(cb: CallbackQuery, callback_data: Mine, repo: Repository, settings: Settings) -> None:
    b = await _own(repo, callback_data.id, cb.from_user.id)
    service = SERVICES_BY_ID.get(b.service_id) if b else None
    try:
        day = date.fromisoformat(callback_data.v)
    except ValueError:
        day = None
    if b is None or not b.is_active or service is None or day is None:
        await ack(cb, "Кнопка устарела. Откройте запись заново.", alert=True)
        return
    now = availability.local_now(settings.studio)
    own = (from_iso(b.start_at), from_iso(b.end_at)) if b.start_at and b.end_at else None
    slots = await availability.slots_for_day(repo, settings.studio, service, day, now, ignore=own)
    if not slots:
        await ack(cb, "На эту дату свободного времени уже нет.", alert=True)
        await cb_resched(cb, Mine(a="resched", id=b.id), repo, settings)
        return
    await respond(cb, f"🔁 <b>Запись №{b.id}</b> · {esc(format_date_ru(day))}\nВыберите время:",
                  ckb.resched_times_kb(b.id, day, slots))


@router.callback_query(Mine.filter(F.a == "rtime"), StateFilter("*"))
async def cb_resched_time(cb: CallbackQuery, callback_data: Mine, repo: Repository, settings: Settings) -> None:
    b = await _own(repo, callback_data.id, cb.from_user.id)
    service = SERVICES_BY_ID.get(b.service_id) if b else None
    try:
        day_s, hhmm = callback_data.v.split("_")
        start = datetime.strptime(f"{day_s} {hhmm}", "%Y-%m-%d %H%M")
    except ValueError:
        start = None
    if b is None or not b.is_active or service is None or start is None:
        await ack(cb, "Кнопка устарела. Откройте запись заново.", alert=True)
        return
    studio = settings.studio
    now = availability.local_now(studio)
    own = (from_iso(b.start_at), from_iso(b.end_at)) if b.start_at and b.end_at else None
    # время должно быть одним из реально свободных слотов (callback_data можно подделать)
    if start not in await availability.slots_for_day(repo, studio, service, start.date(), now, ignore=own):
        await ack(cb, "Это время уже занято. Выберите другое.", alert=True)
        await cb_resched_date(cb, Mine(a="rdate", id=b.id, v=start.date().isoformat()), repo, settings)
        return
    s, e = availability.interval_of(service, start, studio)
    now_utc = datetime.now(timezone.utc)
    try:
        updated = await repo.reschedule(b.id, cb.from_user.id, s, e, posts=studio.posts,
                                        jobs=planning.reminder_jobs(s, now_utc, studio))
    except SlotTaken:
        await ack(cb, "Это время только что заняли. Выберите другое.", alert=True)
        await cb_resched_date(cb, Mine(a="rdate", id=b.id, v=start.date().isoformat()), repo, settings)
        return
    except NotAllowed:
        await ack(cb, "Эту запись уже нельзя перенести.", alert=True)
        return
    await ack(cb)
    await respond(cb, f"✅ <b>Запись №{b.id} перенесена</b>\n{esc(format_when(updated))}\n\n"
                      "Менеджер подтвердит новое время. Напоминание придёт заранее.",
                  ckb.booking_card_kb(updated))
    await notify_admins_text(
        cb.bot, settings.admin_ids,
        f"🔁 <b>Клиент перенёс запись №{b.id}</b>\nБыло: {esc(format_when(b))}\nСтало: {esc(format_when(updated))}\n"
        f"{esc(b.name)} · {esc(b.phone)}", booking_actions_kb(updated))


# --------------------------------------------------------- скидки, уведомления
def _local_date(iso_utc: str, settings: Settings) -> str:
    from zoneinfo import ZoneInfo
    return datetime.fromisoformat(iso_utc).astimezone(ZoneInfo(settings.studio.timezone)).strftime("%d.%m.%Y")


@router.callback_query(Cab.filter(F.a == "offers"), StateFilter("*"))
async def cb_offers(cb: CallbackQuery, state: FSMContext, repo: Repository, settings: Settings) -> None:
    await reset_flow(cb, state)
    now = datetime.now(timezone.utc)
    vouchers = await repo.active_vouchers(cb.from_user.id, now)
    lines = ["🎁 <b>Скидки и предложения</b>", ""]
    if vouchers:
        lines.append("<b>Ваши персональные скидки</b>")
        for v in vouchers:
            what = f"{v.value}%" if v.kind == "percent" else f"{v.value} ₽"
            extra = f", от {v.min_price} ₽" if v.min_price else ""
            lines.append(f"• {esc(v.title)}: −{what}{extra} — до {_local_date(v.expires_at, settings)}")
        lines += ["", "Скидка применится автоматически при оформлении заявки — её можно выключить на последнем шаге."]
    else:
        lines.append("Сейчас персональных скидок нет. Они появляются у постоянных клиентов и при долгом перерыве между визитами.")
    rules = [r for r in await repo.list_rules(active_only=True) if r.trigger == "visits"]
    if rules:
        lines += ["", "<b>Как получить</b>"] + [f"• {esc(r.title)}: после {r.threshold}-го завершённого визита" for r in rules]
    await respond(cb, "\n".join(lines), ckb.offers_kb())


async def _notify_screen(event: Event, repo: Repository) -> None:
    user = await repo.get_user(event.from_user.id)
    enabled = bool(user and user.marketing_consent)
    state_txt = "включены ✅" if enabled else "отключены"
    await respond(event, (
        f"🔔 <b>Персональные предложения и напоминания об уходе: {state_txt}</b>\n\n"
        "Если включить, мы будем изредка присылать скидки и рекомендации по уходу за вашим автомобилем. "
        "Напоминания о записях и сообщения по вашим заявкам приходят всегда. Отключить можно в любой момент — "
        "здесь или командой /stop."), ckb.notify_kb(enabled))


@router.callback_query(Cab.filter(F.a == "notify"), StateFilter("*"))
async def cb_notify(cb: CallbackQuery, repo: Repository) -> None:
    await _notify_screen(cb, repo)


@router.callback_query(Cab.filter(F.a.in_({"notify_on", "notify_off"})), StateFilter("*"))
async def cb_notify_toggle(cb: CallbackQuery, callback_data: Cab, repo: Repository) -> None:
    await repo.set_marketing(cb.from_user.id, callback_data.a == "notify_on")
    await ack(cb, "Готово")
    await _notify_screen(cb, repo)


@router.message(Command("stop"), StateFilter("*"))
async def cmd_stop(message: Message, repo: Repository) -> None:
    await repo.set_marketing(message.from_user.id, False)
    await message.answer("Хорошо, персональные предложения и рекомендации отключены. "
                         "Напоминания о ваших записях по-прежнему будут приходить. Включить снова: «Мой кабинет → Уведомления».")
