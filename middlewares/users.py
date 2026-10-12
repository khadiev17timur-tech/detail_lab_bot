"""Регистрирует пользователя при любом сообщении/нажатии и отсекает не-личные чаты."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from database.repository import Repository

log = logging.getLogger(__name__)


class UserMiddleware(BaseMiddleware):
    def __init__(self, repo: Repository):
        self._repo = repo

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: dict[str, Any]) -> Any:
        chat = event.chat if isinstance(event, Message) else (
            event.message.chat if isinstance(event, CallbackQuery) and event.message else None)
        if chat is not None and chat.type != "private":
            return None  # бот работает только в личных сообщениях
        user = getattr(event, "from_user", None)
        if user is not None and not user.is_bot:
            try:
                await self._repo.upsert_user(user.id, user.username, user.full_name)
            except Exception:
                log.exception("Не удалось сохранить пользователя %s", user.id)
        return await handler(event, data)
