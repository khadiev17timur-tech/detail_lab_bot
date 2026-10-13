"""Миграции схемы SQLite.

Версия хранится в `PRAGMA user_version`. Каждая миграция:
  * выполняется в ОДНОЙ транзакции (при ошибке — полный откат, база остаётся прежней версии);
  * перед миграцией существующей базы автоматически создаётся копия `backups/pre-migration-*.db`;
  * заканчивается проверкой внешних ключей (`PRAGMA foreign_key_check`).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

import aiosqlite

log = logging.getLogger(__name__)

MIGRATION_V2 = """
-- Автомобили клиентов
CREATE TABLE cars (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(tg_id),
    title       TEXT NOT NULL,
    title_key   TEXT NOT NULL,                 -- нормализованное название: защита от дублей
    car_class_id TEXT,
    created_at  TEXT NOT NULL,
    archived    INTEGER NOT NULL DEFAULT 0,
    UNIQUE (user_id, title_key)
);

-- Заявки: пересборка, чтобы расширить список статусов и добавить поля календаря
CREATE TABLE bookings_new (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id         TEXT NOT NULL UNIQUE,
    user_id          INTEGER NOT NULL REFERENCES users(tg_id),
    service_id       TEXT NOT NULL,
    service_title    TEXT NOT NULL,
    car              TEXT NOT NULL,
    car_class_id     TEXT NOT NULL,
    car_class_title  TEXT NOT NULL,
    visit_date       TEXT NOT NULL,
    visit_time       TEXT NOT NULL,
    name             TEXT NOT NULL,
    phone            TEXT NOT NULL,
    comment          TEXT NOT NULL DEFAULT '',
    estimate_rub     INTEGER,
    addons           TEXT NOT NULL DEFAULT '[]',
    status           TEXT NOT NULL DEFAULT 'new'
                     CHECK (status IN ('new','in_work','accepted','rejected','done','cancelled')),
    consent          INTEGER NOT NULL DEFAULT 0,
    consent_at       TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    -- новое в v2
    start_at         TEXT,                      -- YYYY-MM-DDTHH:MM по времени студии; NULL у старых заявок «любое время»
    end_at           TEXT,
    car_id           INTEGER REFERENCES cars(id),
    voucher_id       INTEGER,
    discount_rub     INTEGER NOT NULL DEFAULT 0,
    client_confirmed_at TEXT,
    cancelled_by     TEXT,                      -- client | admin
    reschedule_count INTEGER NOT NULL DEFAULT 0
);
INSERT INTO bookings_new (id, draft_id, user_id, service_id, service_title, car, car_class_id, car_class_title,
    visit_date, visit_time, name, phone, comment, estimate_rub, addons, status, consent, consent_at,
    created_at, updated_at)
SELECT id, draft_id, user_id, service_id, service_title, car, car_class_id, car_class_title,
    visit_date, visit_time, name, phone, comment, estimate_rub, addons, status, consent, consent_at,
    created_at, updated_at
FROM bookings;
DROP TABLE bookings;
ALTER TABLE bookings_new RENAME TO bookings;
CREATE INDEX idx_bookings_status  ON bookings(status);
CREATE INDEX idx_bookings_user    ON bookings(user_id);
CREATE INDEX idx_bookings_created ON bookings(created_at);
CREATE INDEX idx_bookings_start   ON bookings(start_at);

-- Согласие на рекламные сообщения
ALTER TABLE users ADD COLUMN marketing_consent INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN marketing_consent_at TEXT;
ALTER TABLE users ADD COLUMN marketing_asked INTEGER NOT NULL DEFAULT 0;

-- Устойчивая очередь заданий: напоминания, запросы отзывов, рекомендации по уходу
CREATE TABLE jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,                  -- remind:<часы> | review | care:<правило>
    booking_id  INTEGER NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL,
    due_at      TEXT NOT NULL,                  -- UTC ISO
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','sending','sent','skipped','failed','cancelled')),
    attempts    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    sent_at     TEXT,
    note        TEXT,
    UNIQUE (booking_id, kind)                   -- одно и то же задание нельзя создать дважды
);
CREATE INDEX idx_jobs_due ON jobs(status, due_at);

-- Отзывы: один отзыв на визит
CREATE TABLE reviews (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id  INTEGER NOT NULL UNIQUE REFERENCES bookings(id),
    user_id     INTEGER NOT NULL,
    rating      INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment     TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

-- Правила скидок (настраивает администратор) и выданные клиентам купоны
CREATE TABLE discount_rules (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    title          TEXT NOT NULL,
    kind           TEXT NOT NULL CHECK (kind IN ('percent','amount')),
    value          INTEGER NOT NULL CHECK (value > 0),
    trigger        TEXT NOT NULL CHECK (trigger IN ('visits','winback')),
    threshold      INTEGER NOT NULL CHECK (threshold >= 1),   -- число визитов или дней без визита
    valid_days     INTEGER NOT NULL CHECK (valid_days >= 1),
    per_client_limit INTEGER NOT NULL DEFAULT 1,
    min_price      INTEGER NOT NULL DEFAULT 0,
    active         INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL
);
CREATE TABLE vouchers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id     INTEGER NOT NULL REFERENCES discount_rules(id),
    user_id     INTEGER NOT NULL REFERENCES users(tg_id),
    title       TEXT NOT NULL,
    kind        TEXT NOT NULL,
    value       INTEGER NOT NULL,
    min_price   INTEGER NOT NULL DEFAULT 0,
    issued_at   TEXT NOT NULL,
    expires_at  TEXT NOT NULL,                  -- UTC ISO
    status      TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active','reserved','used','revoked')),
    booking_id  INTEGER,
    used_at     TEXT
);
CREATE INDEX idx_vouchers_user ON vouchers(user_id, status);

-- Закрытые даты (отпуск, праздники, ремонт)
CREATE TABLE schedule_blocks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date_from   TEXT NOT NULL,
    date_to     TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
"""


def car_key(title: str) -> str:
    """Ключ для сравнения названий авто: без регистра и лишних пробелов."""
    return re.sub(r"\s+", " ", title or "").strip().casefold()


async def _backup_before(conn: aiosqlite.Connection, backup_dir: Path, old: int, new: int) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = backup_dir / f"pre-migration-v{old}-to-v{new}-{stamp}.db"
    await conn.commit()
    await conn.execute("VACUUM INTO ?", (str(path),))
    log.warning("Перед миграцией схемы v%s→v%s сохранена копия базы: %s", old, new, path)
    return path


async def _fill_v2_from_legacy(conn: aiosqlite.Connection) -> None:
    """Заполняет start_at/end_at и связывает старые заявки с автомобилями клиентов."""
    from content.catalog import SERVICES_BY_ID
    from content.studio import STUDIO
    from services.schedule import service_end, to_iso

    async with conn.execute(
        "SELECT id, user_id, service_id, car, car_class_id, visit_date, visit_time, created_at FROM bookings"
    ) as cur:
        rows = await cur.fetchall()
    for r in rows:
        start_iso = end_iso = None
        if re.fullmatch(r"\d{2}:\d{2}", r["visit_time"] or ""):
            try:
                start = datetime.strptime(f"{r['visit_date']}T{r['visit_time']}", "%Y-%m-%dT%H:%M")
                service = SERVICES_BY_ID.get(r["service_id"])
                end = service_end(service, start, STUDIO) if service else start + timedelta(hours=1)
                start_iso, end_iso = to_iso(start), to_iso(end)
            except ValueError:
                pass
        key = car_key(r["car"])
        car_id = None
        if key:
            await conn.execute(
                "INSERT OR IGNORE INTO cars (user_id, title, title_key, car_class_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (r["user_id"], r["car"], key, r["car_class_id"], r["created_at"]))
            async with conn.execute("SELECT id FROM cars WHERE user_id = ? AND title_key = ?", (r["user_id"], key)) as c2:
                car_id = (await c2.fetchone())["id"]
        await conn.execute("UPDATE bookings SET start_at = ?, end_at = ?, car_id = ? WHERE id = ?",
                           (start_iso, end_iso, car_id, r["id"]))


async def _migrate_v2(conn: aiosqlite.Connection) -> None:
    for statement in _split(MIGRATION_V2):
        await conn.execute(statement)
    await _fill_v2_from_legacy(conn)


def _split(script: str) -> list[str]:
    """Режет SQL-скрипт на отдельные команды (в наших скриптах нет ';' внутри строк и триггеров)."""
    lines = [re.sub(r"--.*$", "", ln) for ln in script.splitlines()]      # комментарии могут содержать ';'
    return [part.strip() for part in "\n".join(lines).split(";") if part.strip()]


MIGRATIONS = {2: _migrate_v2}


async def run_migrations(conn: aiosqlite.Connection, path: str, current: int, *,
                         backup_dir: Path | None) -> None:
    """Последовательно применяет миграции current+1 … последняя. Каждая — атомарно."""
    target = max(MIGRATIONS)
    if backup_dir is not None and current < target:
        async with conn.execute("SELECT COUNT(*) FROM users") as cur:
            has_data = (await cur.fetchone())[0] > 0
        if has_data:
            await _backup_before(conn, backup_dir, current, target)

    await conn.commit()                               # вне транзакции: PRAGMA foreign_keys иначе не сработает
    for version in range(current + 1, target + 1):
        await conn.execute("PRAGMA foreign_keys = OFF")   # нужно для пересборки таблицы
        await conn.execute("BEGIN IMMEDIATE")             # явная транзакция: весь шаг — всё или ничего
        try:
            await MIGRATIONS[version](conn)
            async with conn.execute("PRAGMA foreign_key_check") as cur:
                broken = await cur.fetchall()
            if broken:
                raise RuntimeError(f"Миграция v{version}: нарушены внешние ключи ({len(broken)} строк)")
            await conn.execute(f"PRAGMA user_version = {version}")
            await conn.execute("COMMIT")
            log.info("Схема базы обновлена до версии %s", version)
        except BaseException:
            await conn.execute("ROLLBACK")
            log.error("Миграция v%s не применена, изменения отменены", version)
            raise
        finally:
            await conn.execute("PRAGMA foreign_keys = ON")
