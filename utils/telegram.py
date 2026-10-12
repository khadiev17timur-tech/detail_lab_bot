"""Безопасные обёртки над вызовами Telegram API."""
from __future__ import annotations

import logging
from typing import Union

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import (CallbackQuery, InlineKeyboardMarkup, Message,
                           ReplyKeyboardMarkup, ReplyKeyboardRemove)

log = logging.getLogger(__name__)

Event = Union[Message, CallbackQuery]
Markup = Union[InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, None]


async def ack(cb: CallbackQuery, text: str | None = None, *, alert: bool = False) -> None:
    """Отвечает на callback; устаревший запрос — не ошибка."""
    try:
        await cb.answer(text, show_alert=alert)
    except TelegramBadRequest as exc:
        log.debug("callback.answer пропущен: %s", exc)


def message_of(event: Event) -> Message | None:
    msg = event.message if isinstance(event, CallbackQuery) else event
    return msg if isinstance(msg, Message) else None


async def send(event: Event, text: str, reply_markup: Markup = None) -> Message:
    """Отправляет новое сообщение в чат события."""
    msg = message_of(event)
    if msg is not None:
        return await msg.answer(text, reply_markup=reply_markup)
    return await event.bot.send_message(event.from_user.id, text, reply_markup=reply_markup)


async def respond(event: Event, text: str, reply_markup: Markup = None, *, edit: bool = True) -> Message:
    """Для callback — редактирует сообщение с кнопкой, иначе (или если нельзя) — отправляет новое."""
    if isinstance(event, CallbackQuery):
        await ack(event)
        msg = message_of(event)
        if edit and msg is not None and isinstance(reply_markup, (InlineKeyboardMarkup, type(None))):
            try:
                result = await msg.edit_text(text, reply_markup=reply_markup)
                return result if isinstance(result, Message) else msg
            except TelegramBadRequest as exc:
                if "message is not modified" in str(exc):
                    return msg
                # нельзя править (например, это фото) — отправим заново ниже
                log.debug("edit_text не удался (%s), отправляю новое сообщение", exc)
                try:
                    await msg.delete()
                except TelegramAPIError:
                    pass
    return await send(event, text, reply_markup)


async def strip_markup(bot, chat_id: int, message_id: int | None) -> None:
    """Убирает inline-кнопки у старого сообщения, чтобы на них нельзя было нажать повторно."""
    if not message_id:
        return
    try:
        await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
    except TelegramAPIError:
        pass


async def drop_reply_keyboard(event: Event) -> None:
    """Прячет reply-клавиатуру: отправляем служебное сообщение и сразу удаляем его."""
    try:
        tmp = await send(event, "…", ReplyKeyboardRemove())
        await tmp.delete()
    except TelegramAPIError as exc:
        log.debug("Не удалось скрыть reply-клавиатуру: %s", exc)
