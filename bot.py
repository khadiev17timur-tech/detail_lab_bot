"""Точка входа: python bot.py [--check]"""
from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError, TelegramUnauthorizedError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat, ErrorEvent

from config import ConfigError, Settings, load_settings
from database import Database, Repository
from handlers import build_root_router
from middlewares.users import UserMiddleware
from services.admin import AdminService, PermissionDenied
from utils.logging_setup import setup_logging

log = logging.getLogger("detail_lab")

USER_COMMANDS = [
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="services", description="Каталог услуг"),
    BotCommand(command="prices", description="Прайс-лист"),
    BotCommand(command="calc", description="Рассчитать стоимость"),
    BotCommand(command="book", description="Оставить заявку"),
    BotCommand(command="portfolio", description="Примеры работ"),
    BotCommand(command="faq", description="Частые вопросы"),
    BotCommand(command="contacts", description="Контакты"),
    BotCommand(command="manager", description="Связаться с менеджером"),
    BotCommand(command="cancel", description="Отменить действие"),
]


async def on_error(event: ErrorEvent) -> bool:
    """Глобальный перехват ошибок: пишем в лог и вежливо сообщаем пользователю."""
    exc = event.exception
    update = event.update
    if isinstance(exc, PermissionDenied):
        log.warning("Отказ в доступе: %s", exc)
        text = "⛔ Недостаточно прав."
    else:
        log.exception("Необработанная ошибка при обработке update %s", update.update_id, exc_info=exc)
        text = "😔 Произошла временная ошибка. Попробуйте ещё раз или откройте главное меню: /start"
    try:
        if update.callback_query:
            await update.callback_query.answer(text, show_alert=True)
        elif update.message:
            await update.message.answer(text)
    except TelegramAPIError:
        pass
    return True


def build_dispatcher(settings: Settings, repo: Repository) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.workflow_data.update(settings=settings, repo=repo, admin=AdminService(repo, settings.admin_ids))
    user_mw = UserMiddleware(repo)
    dp.message.outer_middleware(user_mw)
    dp.callback_query.outer_middleware(user_mw)
    dp.include_router(build_root_router())
    dp.errors.register(on_error)
    return dp


def build_bot(settings: Settings) -> Bot:
    # aiogram кэширует DNS на час; при VPN/смене сети адрес api.telegram.org меняется, и бот
    # «залипает» на мёртвом IP. Короткий TTL лечит это без перезапуска.
    session = AiohttpSession()
    session._connector_init["ttl_dns_cache"] = 30
    return Bot(
        token=settings.bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )


async def setup_commands(bot: Bot, settings: Settings) -> None:
    try:
        await bot.set_my_commands(USER_COMMANDS)
        admin_commands = USER_COMMANDS + [BotCommand(command="admin", description="Панель администратора")]
        for admin_id in settings.admin_ids:
            try:
                await bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=admin_id))
            except TelegramAPIError:
                pass  # админ ещё не открывал бота — команда /admin всё равно работает
    except TelegramAPIError as exc:
        log.warning("Не удалось установить список команд: %s", exc)


# Коды выхода. systemd-юнит не перезапускает сервис при 2 и 3 (бессмысленно: нужно править настройки).
EXIT_OK, EXIT_CONFIG, EXIT_TOKEN = 0, 2, 3


async def wait_for_telegram(bot: Bot):
    """get_me с ожиданием сети: после загрузки сервера интернет может появиться не сразу."""
    delay = 2.0
    while True:
        try:
            return await bot.get_me()
        except TelegramNetworkError as exc:
            log.warning("Telegram пока недоступен (%s). Повтор через %.0f с…", type(exc).__name__, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)


async def main(check_only: bool = False) -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    setup_logging(settings.log_level, secrets=[settings.bot_token])
    if not settings.admin_ids:
        log.warning("ADMIN_IDS пуст: панель /admin и уведомления о заявках недоступны")

    db = Database(settings.db_path)
    await db.connect()
    repo = Repository(db)
    bot = build_bot(settings)
    try:
        dp = build_dispatcher(settings, repo)
        if check_only:
            log.info("Проверка пройдена: конфигурация, база данных и обработчики в порядке.")
            return EXIT_OK
        try:
            me = await wait_for_telegram(bot)
        except TelegramUnauthorizedError:
            log.error("Telegram отклонил токен (Unauthorized). Проверьте BOT_TOKEN в .env.")
            return EXIT_TOKEN
        # Если когда-то был включён webhook, long polling с ним конфликтует. Очередь апдейтов не трогаем.
        await bot.delete_webhook(drop_pending_updates=False)
        log.info("Бот @%s запущен. Админов: %d. База: %s", me.username, len(settings.admin_ids), settings.db_path)
        await setup_commands(bot, settings)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
        log.info("Остановка по сигналу: polling завершён")
        return EXIT_OK
    finally:
        await bot.session.close()
        await db.close()


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main(check_only="--check" in sys.argv)))
    except KeyboardInterrupt:
        print("Остановлено.")
