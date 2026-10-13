"""Административная панель. Доступ проверяется и фильтром, и самим AdminService."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import BaseFilter, Command
from aiogram.types import BufferedInputFile, CallbackQuery, FSInputFile, Message, TelegramObject

from config import Settings
from database.models import STATUS_LABELS, BookingStatus, parse_status
from keyboards import admin as akb
from keyboards.callbacks import Adm, AdmX
from services.admin import LAST_N, AdminService
from services.backup import BackupError
from services.formatting import admin_booking_card, stats_text
from services.lifecycle import after_done
from services.notifications import notify_client_status
from utils.telegram import ack, message_of, respond
from utils.timeutils import now_local

log = logging.getLogger(__name__)

NO_ACCESS = "⛔ У вас нет доступа к панели администратора."


class IsAdmin(BaseFilter):
    """Админ — только в личном чате с ботом."""

    async def __call__(self, event: TelegramObject, admin: AdminService) -> bool:
        user = getattr(event, "from_user", None)
        msg = message_of(event) if isinstance(event, CallbackQuery) else event
        chat = getattr(msg, "chat", None)
        if chat is not None and chat.type != "private":
            return False
        return admin.is_admin(user.id if user else None)


admin_router = Router(name="admin")
admin_router.message.filter(IsAdmin())
admin_router.callback_query.filter(IsAdmin())

# Отдельный роутер отказа: подключается ПОСЛЕ admin_router и ловит всех, кто не прошёл фильтр.
deny_router = Router(name="admin_deny")


def _filter_to_status(flt: str) -> tuple[BookingStatus | None, str]:
    if flt == "new":
        return BookingStatus.NEW, "🆕 Новые заявки"
    status = parse_status(flt)
    if status is not None:
        return status, f"Заявки: {STATUS_LABELS[status]}"
    return None, "📋 Все заявки"


async def show_panel(event, admin: AdminService, uid: int) -> None:
    new = await admin.new_count(uid)
    total = await admin.total_count(uid)
    text = (f"🛠 <b>Панель администратора</b>\n\nНовых заявок: <b>{new}</b>\nВсего заявок: {total}\n\n"
            "Выберите действие:")
    await respond(event, text, akb.panel_kb(new))


@admin_router.message(Command("admin"))
async def cmd_admin(message: Message, admin: AdminService) -> None:
    await show_panel(message, admin, message.from_user.id)


@admin_router.callback_query(Adm.filter(F.a == "home"))
async def cb_home(cb: CallbackQuery, admin: AdminService) -> None:
    await ack(cb)
    await show_panel(cb, admin, cb.from_user.id)


@admin_router.callback_query(Adm.filter(F.a == "close"))
async def cb_close(cb: CallbackQuery) -> None:
    await ack(cb)
    msg = message_of(cb)
    if msg:
        try:
            await msg.delete()
        except TelegramAPIError:
            pass


@admin_router.callback_query(Adm.filter(F.a == "filters"))
async def cb_filters(cb: CallbackQuery) -> None:
    await respond(cb, "🔎 <b>Фильтр по статусу</b>", akb.filters_kb())


@admin_router.callback_query(Adm.filter(F.a.in_({"list", "last"})))
async def cb_list(cb: CallbackQuery, callback_data: Adm, admin: AdminService) -> None:
    uid = cb.from_user.id
    flt = "last" if callback_data.a == "last" else (callback_data.f or "all")
    if flt == "last":
        items = await admin.last(uid, LAST_N)
        text = f"🕒 <b>Последние {LAST_N} заявок</b>" if items else "🕒 Заявок пока нет."
        await respond(cb, text, akb.list_kb(items, "last", 0, 1, show_pager=False))
        return
    status, title = _filter_to_status(flt)
    page = await admin.list_page(uid, status, callback_data.p)
    if page.total == 0:
        await respond(cb, f"<b>{title}</b>\n\nЗаявок нет.", akb.list_kb([], flt, 0, 1))
        return
    text = f"<b>{title}</b>\nВсего: {page.total} · страница {page.page + 1} из {page.pages}"
    await respond(cb, text, akb.list_kb(page.items, flt, page.page, page.pages))


async def _show_booking(cb: CallbackQuery, admin: AdminService, settings: Settings, booking_id: int,
                        back_filter: str = "all", back_page: int = 0) -> None:
    uid = cb.from_user.id
    booking = await admin.get(uid, booking_id)
    if booking is None:
        await ack(cb, "Заявка не найдена.", alert=True)
        return
    history = await admin.history(uid, booking_id)
    text = admin_booking_card(booking, tz_name=settings.studio.timezone, history=history)
    await respond(cb, text, akb.booking_view_kb(booking, back_filter or "all", back_page))


@admin_router.callback_query(Adm.filter(F.a == "view"))
async def cb_view(cb: CallbackQuery, callback_data: Adm, admin: AdminService, settings: Settings) -> None:
    await _show_booking(cb, admin, settings, callback_data.id, callback_data.f, callback_data.p)


@admin_router.callback_query(Adm.filter(F.a == "set"))
async def cb_set_status(cb: CallbackQuery, callback_data: Adm, admin: AdminService, settings: Settings) -> None:
    uid = cb.from_user.id
    new_status = parse_status(callback_data.s)
    if new_status is None:
        await ack(cb, "Неизвестный статус.", alert=True)
        return
    expected = parse_status(callback_data.f) if callback_data.f else None
    result = await admin.change_status(uid, callback_data.id, new_status, expected)
    if result.booking is None:
        await ack(cb, "Заявка не найдена.", alert=True)
        return
    if not result.changed:
        if expected is not None and result.old_status != expected:
            await ack(cb, f"Заявка уже обработана: {STATUS_LABELS[result.old_status]}", alert=True)
        else:
            await ack(cb, "Этот статус уже установлен.")
        await _show_booking(cb, admin, settings, callback_data.id)
        return

    if new_status == BookingStatus.DONE:
        await after_done(cb.bot, admin._repo, result.booking, settings.studio)
    delivered = await notify_client_status(cb.bot, result.booking, settings.studio.name)
    if delivered:
        await ack(cb, f"Статус: {STATUS_LABELS[new_status]}")
    else:
        await ack(cb, "Статус обновлён, но клиент не получил сообщение (возможно, он заблокировал бота).",
                  alert=True)
    await _show_booking(cb, admin, settings, callback_data.id)


@admin_router.callback_query(Adm.filter(F.a == "stats"))
async def cb_stats(cb: CallbackQuery, admin: AdminService) -> None:
    stats = await admin.stats(cb.from_user.id)
    await respond(cb, stats_text(stats), akb.back_to_panel_kb())


@admin_router.callback_query(Adm.filter(F.a == "csv"))
async def cb_csv(cb: CallbackQuery, admin: AdminService, settings: Settings) -> None:
    data = await admin.export_csv(cb.from_user.id)
    msg = message_of(cb)
    if msg is None:
        await ack(cb, "Не удалось отправить файл.", alert=True)
        return
    await ack(cb, "Готовлю файл…")
    stamp = now_local(settings.studio.timezone).strftime("%Y%m%d_%H%M")
    await msg.answer_document(
        BufferedInputFile(data, filename=f"detail_lab_bookings_{stamp}.csv"),
        caption="📤 Выгрузка заявок. Разделитель — «;», кодировка UTF-8 (открывается в Excel).",
    )


async def _send_backup(event, admin: AdminService, settings: Settings) -> None:
    uid = event.from_user.id
    msg = message_of(event)
    if msg is None:
        return
    try:
        path = await admin.backup(uid, settings.backup_dir, settings.backup_keep)
    except BackupError as exc:
        log.error("Ручной бэкап не удался: %s", exc)
        await msg.answer("⚠️ Не удалось сделать резервную копию. Подробности — в логах бота.")
        return
    await msg.answer_document(
        FSInputFile(path),
        caption=("💾 Резервная копия базы (SQLite, сжата gzip). Храните её в надёжном месте: в ней данные клиентов. "
                 "Распаковать: gunzip " + path.name),
    )


@admin_router.message(Command("backup"))
async def cmd_backup(message: Message, admin: AdminService, settings: Settings) -> None:
    await _send_backup(message, admin, settings)


@admin_router.callback_query(Adm.filter(F.a == "backup"))
async def cb_backup(cb: CallbackQuery, admin: AdminService, settings: Settings) -> None:
    await ack(cb, "Делаю копию…")
    await _send_backup(cb, admin, settings)


# ------------------------------------------------------------------ отказ
@deny_router.message(Command("admin", "backup"))
async def deny_message(message: Message) -> None:
    await message.answer(NO_ACCESS)


@deny_router.callback_query(Adm.filter())
@deny_router.callback_query(AdmX.filter())
async def deny_callback(cb: CallbackQuery) -> None:
    log.warning("Попытка админ-действия без прав: user=%s data=%s", cb.from_user.id, cb.data)
    await ack(cb, NO_ACCESS, alert=True)

