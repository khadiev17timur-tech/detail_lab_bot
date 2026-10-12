"""Запуск процесса: ожидание сети и коды выхода (на них опирается systemd-юнит)."""
import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import GetMe

import bot as bot_module
from tests.conftest import FakeTelegram, TOKEN


@pytest.mark.asyncio
async def test_wait_for_telegram_retries_until_network_returns(monkeypatch):
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(bot_module.asyncio, "sleep", fake_sleep)

    class FlakySession(FakeTelegram):
        attempts = 0

        async def make_request(self, bot, method, timeout=None):
            if isinstance(method, GetMe):
                FlakySession.attempts += 1
                if FlakySession.attempts <= 3:
                    raise TelegramNetworkError(method, "нет сети")
                from aiogram.types import User
                return User(id=1, is_bot=True, first_name="b", username="test_bot")
            return await super().make_request(bot, method, timeout)

    me = await bot_module.wait_for_telegram(Bot(TOKEN, session=FlakySession()))
    assert me.username == "test_bot" and FlakySession.attempts == 4
    assert sleeps == [2.0, 4.0, 8.0]          # пауза растёт, но не бесконечно


def test_exit_codes_are_distinct():
    assert (bot_module.EXIT_OK, bot_module.EXIT_CONFIG, bot_module.EXIT_TOKEN) == (0, 2, 3)


@pytest.mark.asyncio
async def test_main_returns_config_exit_code(monkeypatch, tmp_path):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.setattr(bot_module, "load_settings",
                        lambda: (_ for _ in ()).throw(bot_module.ConfigError("нет токена")))
    assert await bot_module.main() == bot_module.EXIT_CONFIG
