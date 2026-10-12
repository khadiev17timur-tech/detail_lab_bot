"""Подключение к SQLite и создание схемы."""
from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    tg_id       INTEGER PRIMARY KEY,
    username    TEXT,
    full_name   TEXT,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bookings (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id         TEXT NOT NULL UNIQUE,      -- защита от повторной отправки
    user_id          INTEGER NOT NULL REFERENCES users(tg_id),
    service_id       TEXT NOT NULL,
    service_title    TEXT NOT NULL,
    car              TEXT NOT NULL,
    car_class_id     TEXT NOT NULL,
    car_class_title  TEXT NOT NULL,
    visit_date       TEXT NOT NULL,             -- YYYY-MM-DD
    visit_time       TEXT NOT NULL,             -- HH:MM или any
    name             TEXT NOT NULL,
    phone            TEXT NOT NULL,
    comment          TEXT NOT NULL DEFAULT '',
    estimate_rub     INTEGER,
    addons           TEXT NOT NULL DEFAULT '[]', -- JSON-список названий
    status           TEXT NOT NULL DEFAULT 'new'
                     CHECK (status IN ('new','in_work','accepted','rejected','done')),
    consent          INTEGER NOT NULL DEFAULT 0,
    consent_at       TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bookings_status  ON bookings(status);
CREATE INDEX IF NOT EXISTS idx_bookings_user    ON bookings(user_id);
CREATE INDEX IF NOT EXISTS idx_bookings_created ON bookings(created_at);

CREATE TABLE IF NOT EXISTS booking_status_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id  INTEGER NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
    old_status  TEXT,
    new_status  TEXT NOT NULL,
    changed_by  INTEGER,                         -- Telegram ID администратора; NULL = система
    changed_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_status_log_booking ON booking_status_log(booking_id);
"""


class Database:
    """Одно асинхронное соединение на всё приложение."""

    def __init__(self, path: Path | str):
        self.path = str(path)
        self._conn: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("База данных не подключена: вызовите Database.connect()")
        return self._conn

    async def connect(self) -> None:
        if self._conn is not None:
            return
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self.path)
        try:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA foreign_keys = ON")
            await conn.execute("PRAGMA busy_timeout = 5000")
            if self.path != ":memory:":
                await conn.execute("PRAGMA journal_mode = WAL")
            await conn.executescript(SCHEMA)
            await conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            await conn.commit()
        except Exception:
            await conn.close()
            raise
        self._conn = conn
        log.info("База данных готова: %s", self.path)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            log.info("Соединение с базой закрыто")
