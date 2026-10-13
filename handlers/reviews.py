"""Отзывы: оценка 1–5, комментарий, сигнал администратору о плохой оценке, ссылка на публичную площадку."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from config import Settings
from database.repository import Repository
from keyboards import client as ckb
from keyboards.callbacks import Rev, RevC
from services.formatting import esc, low_rating_alert, review_thanks_text
from services.notifications import notify_admins_text
from services.validators import ValidationError, validate_comment
from states.client import ReviewStates
from utils.telegram import ack, respond

log = logging.getLogger(__name__)
router = Router(name="reviews")

MAX_COMMENT = 500


def _prompt_kb(booking_id: int, url: str, rating: int, low_max: int) -> InlineKeyboardMarkup:
    base = ckb.review_comment_kb(booking_id)
    if url and rating > low_max:
        base.inline_keyboard.insert(0, ckb.review_link_kb(url).inline_keyboard[0])
    return base


@router.callback_query(Rev.filter())
async def cb_rating(cb: CallbackQuery, callback_data: Rev, state: FSMContext, repo: Repository, settings: Settings) -> None:
    studio = settings.studio
    booking = await repo.get_booking(callback_data.id)
    if booking is None or booking.user_id != cb.from_user.id or booking.status.value != "done":
        await ack(cb, "Оценка для этого визита недоступна.", alert=True)
        return
    if not 1 <= callback_data.r <= 5:
        await ack(cb, "Выберите оценку от 1 до 5.", alert=True)
        return
    review = await repo.create_review(booking.id, cb.from_user.id, callback_data.r)
    if review is None:                                   # повторное нажатие / уже оценено
        await ack(cb, "Оценка уже принята, спасибо!", alert=True)
        return
    low = review.rating <= studio.review_low_rating
    await ack(cb)
    await state.set_state(ReviewStates.comment)
    await state.update_data(review_booking_id=booking.id, review_low=low)
    text = review_thanks_text(review.rating, studio.review_url, low)
    text += "\n\nМожно добавить комментарий — просто напишите его сообщением."
    await respond(cb, text, _prompt_kb(booking.id, studio.review_url, review.rating, studio.review_low_rating))
    if low:
        await notify_admins_text(cb.bot, settings.admin_ids, low_rating_alert(booking, review.rating, "", studio.timezone))


@router.callback_query(RevC.filter(F.a == "skip"))
async def cb_skip(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await ack(cb, "Спасибо!")
    await respond(cb, "Спасибо за оценку! Ждём вас снова 🖤", None)


@router.message(ReviewStates.comment, F.text, ~F.text.startswith("/"))
async def msg_comment(message: Message, state: FSMContext, repo: Repository, settings: Settings) -> None:
    data = await state.get_data()
    booking_id = data.get("review_booking_id")
    try:
        comment = validate_comment(message.text, MAX_COMMENT)
    except ValidationError as exc:
        await message.answer(f"⚠️ {exc}")
        return
    await state.clear()
    if not booking_id or not comment:
        await message.answer("Спасибо!")
        return
    saved = await repo.add_review_comment(booking_id, message.from_user.id, comment)
    await message.answer("Спасибо, мы всё прочитаем 🖤" if saved else "Спасибо!")
    if saved and data.get("review_low"):
        booking = await repo.get_booking(booking_id)
        review = await repo.get_review(booking_id)
        if booking and review:
            await notify_admins_text(
                message.bot, settings.admin_ids,
                f"💬 <b>Комментарий к низкой оценке</b> (запись №{booking.id}, {review.rating}/5):\n{esc(comment)}")


@router.message(StateFilter(ReviewStates.comment))
async def msg_non_text(message: Message) -> None:
    await message.answer("Напишите комментарий текстом или нажмите «Пропустить».")
