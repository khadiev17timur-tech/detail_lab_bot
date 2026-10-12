"""Чтение настроек из переменных окружения (.env)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

from content.studio import STUDIO, Studio

BASE_DIR = Path(__file__).resolve().parent

_TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{20,}$")
_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")


class ConfigError(RuntimeError):
    """Понятная ошибка конфигурации — её текст показывается пользователю."""


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_ids: frozenset[int]
    db_path: Path
    portfolio_dir: Path
    log_level: str
    studio: Studio

    def __repr__(self) -> str:  # токен никогда не попадает в логи
        return (
            f"Settings(admin_ids={sorted(self.admin_ids)}, db_path={self.db_path}, "
            f"log_level={self.log_level}, studio={self.studio.name!r}, bot_token=***)"
        )


def parse_admin_ids(raw: str | None) -> frozenset[int]:
    ids: set[int] = set()
    for part in re.split(r"[,\s;]+", raw or ""):
        if not part:
            continue
        try:
            value = int(part)
        except ValueError:
            raise ConfigError(
                f"ADMIN_IDS: «{part}» не похоже на Telegram ID. "
                "Укажите числа через запятую, например: 123456789,987654321."
            ) from None
        if value <= 0:
            raise ConfigError(f"ADMIN_IDS: ID должен быть положительным числом, получено {value}.")
        ids.add(value)
    return frozenset(ids)


def _parse_bool(raw: str | None, default: bool) -> bool:
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "да"}


def _clean_url(name: str, raw: str | None) -> str | None:
    value = (raw or "").strip()
    if not value:
        return None
    if not re.match(r"^https?://\S+$", value):
        raise ConfigError(f"{name}: ссылка должна начинаться с http:// или https://")
    return value


def _build_studio(env: Mapping[str, str]) -> Studio:
    changes: dict[str, object] = {}

    for env_name, field in (
        ("STUDIO_ADDRESS", "address"),
        ("STUDIO_HOURS", "work_hours"),
        ("STUDIO_PHONE", "phone"),
    ):
        value = (env.get(env_name) or "").strip()
        if value:
            changes[field] = value

    manager = (env.get("MANAGER_USERNAME") or "").strip().lstrip("@")
    if manager:
        if not _USERNAME_RE.match(manager):
            raise ConfigError(
                "MANAGER_USERNAME: ожидается username Telegram (5–32 символа: латиница, "
                "цифры, подчёркивание), например: detaillab_manager."
            )
        changes["manager_username"] = manager

    for env_name, field in (("MAP_URL", "map_url"), ("WEBSITE_URL", "website_url")):
        url = _clean_url(env_name, env.get(env_name))
        if url:
            changes[field] = url

    tz = (env.get("TIMEZONE") or "").strip()
    if tz:
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            raise ConfigError(f"TIMEZONE: неизвестный часовой пояс «{tz}» (пример: Europe/Moscow).") from None
        changes["timezone"] = tz

    changes["demo"] = _parse_bool(env.get("DEMO_MODE"), STUDIO.demo)
    return replace(STUDIO, **changes)


def load_settings(env: Mapping[str, str] | None = None, env_file: Path | None = None) -> Settings:
    """Собирает настройки. `env` — для тестов; по умолчанию читаются .env и os.environ."""
    if env is None:
        load_dotenv(env_file or BASE_DIR / ".env")
        env = os.environ

    token = (env.get("BOT_TOKEN") or "").strip()
    if not token:
        raise ConfigError(
            "BOT_TOKEN не задан. Скопируйте .env.example в .env и вставьте токен от @BotFather."
        )
    if not _TOKEN_RE.match(token):
        raise ConfigError(
            "BOT_TOKEN выглядит неверно. Ожидается формат 123456789:AA... — возьмите токен в @BotFather."
        )

    db_raw = (env.get("DB_PATH") or "data/detail_lab.db").strip()
    db_path = Path(db_raw)
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path

    log_level = (env.get("LOG_LEVEL") or "INFO").strip().upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise ConfigError("LOG_LEVEL: допустимо DEBUG, INFO, WARNING или ERROR.")

    return Settings(
        bot_token=token,
        admin_ids=parse_admin_ids(env.get("ADMIN_IDS")),
        db_path=db_path,
        portfolio_dir=BASE_DIR / "assets" / "portfolio",
        log_level=log_level,
        studio=_build_studio(env),
    )
