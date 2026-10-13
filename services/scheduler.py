"""Фоновые задания: напоминания о визите, запрос отзыва, рекомендации по уходу, ежедневные предложения.

Очередь хранится в SQLite (таблица jobs), поэтому после перезапуска ничего не теряется. Защита от лишних сообщений:
  * задание уникально (booking_id, kind) — создать его дважды нельзя;
  * задание «берётся» атомарно (pending → sending), два цикла не отправят одно и то же;
  * опоздавшие из-за простоя напоминания не отправляются (grace), а не «валятся» пачкой;
  * застрявшие в sending после падения помечаются failed, а не отправляются повторно;
  * отправка идёт по одному сообщению с паузой, не более batch за проход.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import Settings
from content.care import CARE_RULES_BY_ID, DISCLAIMER
from content.catalog import SERVICES_BY_ID
from database.models import BookingStatus, Job
from database.repository import Repository, iso_utc
from keyboards import client as ckb
from keyboards.callbacks import Svc
from services import availability
from services.care import recommendations
from services.formatting import esc, reminder_text, review_request_text
from services.lifecycle import daily_winback
from services.schedule import from_iso

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_DELAY = timedelta(minutes=5)
REVIEW_MAX_AGE = timedelta(days=7)
WINBACK_HOUR = 11


class Skip(Exception):
    """Задание не нужно отправлять (причина — в тексте)."""


async def _send(bot: Bot, job: Job, repo: Repository, settings: Settings, now: datetime) -> None:
    studio = settings.studio
    booking = await repo.get_booking(job.booking_id)
    if booking is None or booking.user_id != job.user_id:
        raise Skip("запись не найдена")
    due = datetime.fromisoformat(job.due_at)

    if job.kind.startswith("remind:"):
        hours = int(job.kind.split(":", 1)[1])
        if booking.status not in (BookingStatus.IN_WORK, BookingStatus.ACCEPTED):
            raise Skip(f"статус {booking.status.value}")
        if not booking.start_at or from_iso(booking.start_at) <= availability.local_now(studio, now):
            raise Skip("визит уже начался или время не задано")
        if now - due > timedelta(minutes=studio.reminder_late_grace_min):
            raise Skip("опоздало из-за простоя")
        await bot.send_message(booking.user_id, reminder_text(booking, hours), reply_markup=ckb.reminder_kb(booking))

    elif job.kind == "review":
        if booking.status != BookingStatus.DONE:
            raise Skip("визит не завершён")
        if await repo.get_review(booking.id) is not None:
            raise Skip("отзыв уже есть")
        if now - due > REVIEW_MAX_AGE:
            raise Skip("слишком поздно")
        await bot.send_message(booking.user_id, review_request_text(booking), reply_markup=ckb.rating_kb(booking.id))

    elif job.kind.startswith("care:"):
        rule = CARE_RULES_BY_ID.get(job.kind.split(":", 1)[1])
        user = await repo.get_user(booking.user_id)
        if rule is None:
            raise Skip("правило удалено")
        if user is None or not user.marketing_consent:
            raise Skip("нет согласия на предложения")
        if booking.status != BookingStatus.DONE:
            raise Skip("визит не завершён")
        local = availability.local_now(studio, now)
        done = await repo.done_for_user(booking.user_id, booking.car_id) if booking.car_id else [booking]
        active = [b for b in await repo.upcoming_for_user(booking.user_id, local)
                  if booking.car_id is None or b.car_id == booking.car_id]
        if not any(r.rule.id == rule.id for r in recommendations(done, active, local.date())):
            raise Skip("уже неактуально")
        svc = SERVICES_BY_ID.get(rule.recommend_service)
        markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text=f"🗓 Записаться: {svc.short_title}" if svc else "🗓 Записаться",
            callback_data=Svc(action="book", id=rule.recommend_service).pack())]]) if svc else None
        await bot.send_message(
            booking.user_id,
            f"🚗 <b>{esc(rule.title)}</b>\n\n{esc(rule.text)}\n\n<i>{esc(DISCLAIMER)}</i>\n\n<i>Отключить: /stop</i>",
            reply_markup=markup)
    else:
        raise Skip(f"неизвестный тип {job.kind}")


async def process_jobs(bot: Bot, repo: Repository, settings: Settings, now: datetime | None = None, *,
                       batch: int = 20, pause: float = 0.05) -> dict[str, int]:
    """Один проход планировщика. Возвращает счётчики: sent / skipped / retry / failed."""
    now = now or datetime.now(timezone.utc)
    stats = {"sent": 0, "skipped": 0, "retry": 0, "failed": 0}
    for job in await repo.claim_due_jobs(iso_utc(now), batch):
        try:
            await _send(bot, job, repo, settings, now)
        except Skip as why:
            await repo.finish_job(job.id, "skipped", str(why))
            stats["skipped"] += 1
        except TelegramRetryAfter as exc:
            await repo.retry_job_later(job.id, iso_utc(now + timedelta(seconds=exc.retry_after + 1)), "flood limit")
            stats["retry"] += 1
            break                                        # Telegram просит притормозить — прекращаем проход
        except TelegramForbiddenError:
            await repo.finish_job(job.id, "failed", "клиент заблокировал бота")
            stats["failed"] += 1
        except TelegramAPIError as exc:
            if job.attempts >= MAX_ATTEMPTS:
                await repo.finish_job(job.id, "failed", f"ошибка Telegram: {type(exc).__name__}")
                stats["failed"] += 1
            else:
                await repo.retry_job_later(job.id, iso_utc(now + RETRY_DELAY), f"повтор после {type(exc).__name__}")
                stats["retry"] += 1
        except Exception:
            log.exception("Сбой задания %s (%s)", job.id, job.kind)
            await repo.finish_job(job.id, "failed", "внутренняя ошибка")
            stats["failed"] += 1
        else:
            await repo.finish_job(job.id, "sent")
            stats["sent"] += 1
        if pause:
            await asyncio.sleep(pause)
    return stats


async def scheduler_loop(bot: Bot, repo: Repository, settings: Settings, *, interval: float = 30.0) -> None:
    """Бесконечный цикл. Любая ошибка проглатывается и логируется — планировщик не должен падать."""
    recovered = await repo.recover_stuck_jobs()
    if recovered:
        log.warning("После перезапуска помечено прерванных заданий: %d (повторно не отправляются)", recovered)
    last_winback: str | None = None
    tz = ZoneInfo(settings.studio.timezone)
    while True:
        try:
            stats = await process_jobs(bot, repo, settings)
            if any(stats.values()):
                log.info("Планировщик: %s", stats)
            local = datetime.now(tz)
            if local.hour >= WINBACK_HOUR and last_winback != local.date().isoformat():
                last_winback = local.date().isoformat()
                issued = await daily_winback(bot, repo, settings.studio)
                if issued:
                    log.info("Выдано персональных скидок «давно не были»: %d", len(issued))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Ошибка в цикле планировщика")
        await asyncio.sleep(interval)
