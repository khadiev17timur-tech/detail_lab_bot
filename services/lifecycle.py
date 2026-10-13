"""Что происходит после завершения визита и ежедневные задачи удержания клиентов."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from database.models import Booking, Voucher
from database.repository import JobSpec, Repository
from keyboards.callbacks import Menu
from services import planning
from services.discounts import issue_milestone_vouchers, issue_winback_vouchers
from services.formatting import esc

log = logging.getLogger(__name__)


def done_jobs(booking: Booking, now_utc: datetime, studio) -> list[JobSpec]:
    """Задания, которые ставятся при завершении визита: запрос отзыва и рекомендации по уходу."""
    return [planning.review_job(now_utc, studio), *planning.care_jobs(booking, now_utc)]


def voucher_message(v: Voucher, tz_name: str) -> str:
    what = f"{v.value}%" if v.kind == "percent" else f"{v.value} ₽"
    until = datetime.fromisoformat(v.expires_at).astimezone(ZoneInfo(tz_name)).strftime("%d.%m.%Y")
    extra = f" (на заказ от {v.min_price} ₽)" if v.min_price else ""
    return (f"🎁 <b>Для вас персональная скидка {what}</b>{extra}\n\n{esc(v.title)}\nДействует до {until}. "
            "Применится автоматически при оформлении заявки.\n\n<i>Отключить предложения: /stop</i>")


async def _send_voucher(bot: Bot, repo: Repository, v: Voucher, tz_name: str) -> bool:
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    user = await repo.get_user(v.user_id)
    if user is None or not user.marketing_consent:       # реклама — только с согласия
        return False
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🗓 Записаться со скидкой", callback_data=Menu(action="book").pack())]])
    try:
        await bot.send_message(v.user_id, voucher_message(v, tz_name), reply_markup=markup)
        return True
    except TelegramAPIError as exc:
        log.warning("Не удалось отправить скидку клиенту %s: %s", v.user_id, exc)
        return False


async def after_done(bot: Bot, repo: Repository, booking: Booking, studio, now: datetime | None = None) -> list[Voucher]:
    """Вызывается один раз при переходе заявки в «завершена»: выдаёт купоны за N-й визит."""
    now = now or datetime.now(timezone.utc)
    vouchers = await issue_milestone_vouchers(repo, booking.user_id, now)
    for v in vouchers:
        await _send_voucher(bot, repo, v, studio.timezone)
    return vouchers


async def daily_winback(bot: Bot, repo: Repository, studio, now: datetime | None = None) -> list[Voucher]:
    """«Давно не были»: выдаёт купоны по правилам и сообщает тем, кто согласился на предложения."""
    now = now or datetime.now(timezone.utc)
    vouchers = await issue_winback_vouchers(repo, now)
    for v in vouchers:
        await _send_voucher(bot, repo, v, studio.timezone)
    return vouchers
