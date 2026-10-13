"""Отправка уведомлений администраторам и клиентам."""
from __future__ import annotations

import logging
from typing import Iterable

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from database.models import Booking, BookingStatus
from keyboards.admin import booking_actions_kb
from keyboards.main import new_request_kb
from services.formatting import client_status_text, new_booking_admin_text

log = logging.getLogger(__name__)


async def notify_admins_new_booking(bot: Bot, admin_ids: Iterable[int], booking: Booking,
                                    tz_name: str) -> int:
    """Рассылает заявку всем администраторам. Возвращает число доставленных."""
    ids = list(admin_ids)
    if not ids:
        log.warning("ADMIN_IDS пуст — уведомление по заявке №%s некому отправить", booking.id)
        return 0
    delivered = 0
    text = new_booking_admin_text(booking, tz_name=tz_name)
    for admin_id in ids:
        try:
            await bot.send_message(admin_id, text, reply_markup=booking_actions_kb(booking))
            delivered += 1
        except TelegramAPIError as exc:
            # админ мог не нажать /start у бота или заблокировать его
            log.warning("Не удалось уведомить администратора %s о заявке №%s: %s", admin_id, booking.id, exc)
    return delivered


async def notify_client_status(bot: Bot, booking: Booking, studio_name: str) -> bool:
    """Сообщает клиенту о смене статуса. False — если не уведомили (нет текста или ошибка доставки)."""
    text = client_status_text(booking, studio_name)
    if text is None:
        return True
    try:
        markup = new_request_kb() if booking.status in (BookingStatus.REJECTED, BookingStatus.DONE) else None
        await bot.send_message(booking.user_id, text, reply_markup=markup)
        return True
    except TelegramAPIError as exc:
        log.warning("Не удалось уведомить клиента %s по заявке №%s: %s", booking.user_id, booking.id, exc)
        return False


async def notify_admins_text(bot: Bot, admin_ids: Iterable[int], text: str, reply_markup=None) -> int:
    """Рассылает произвольное сообщение администраторам. Ошибки доставки не прерывают рассылку."""
    delivered = 0
    for admin_id in admin_ids:
        try:
            await bot.send_message(admin_id, text, reply_markup=reply_markup)
            delivered += 1
        except TelegramAPIError as exc:
            log.warning("Не удалось отправить сообщение администратору %s: %s", admin_id, exc)
    return delivered
