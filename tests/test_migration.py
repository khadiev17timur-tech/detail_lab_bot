"""Миграция v1 → v2 на базе, созданной ИСХОДНОЙ версией бота (боевая схема)."""
import sqlite3
from pathlib import Path

import pytest

from database import Database, Repository
from database import migrations
from database.connection import SCHEMA_V1

pytestmark = pytest.mark.asyncio


def make_v1_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(SCHEMA_V1)
    con.execute("PRAGMA user_version = 1")
    con.execute("INSERT INTO users VALUES (10, 'ivan', 'Иван', '2026-10-01T10:00:00+00:00', '2026-10-12T10:00:00+00:00')")
    con.execute("INSERT INTO users VALUES (11, NULL, 'Мария', '2026-10-02T10:00:00+00:00', '2026-10-02T10:00:00+00:00')")
    rows = [
        # id, draft, user, service, title, car, class, ctitle, date, time, name, phone, status
        (1, "d1", 10, "wash", "Мойка", "BMW X5", "suv", "Внедорожник", "2026-10-20", "12:00", "Иван", "+79990000001", "accepted"),
        (2, "d2", 10, "polish", "Полировка", "bmw  x5", "suv", "Внедорожник", "2026-10-22", "10:00", "Иван", "+79990000001", "new"),
        (3, "d3", 11, "wash", "Мойка", "Kia Rio", "compact", "Компактный", "2026-10-21", "any", "Мария", "+79990000002", "rejected"),
        (4, "d4", 11, "interior", "Химчистка", "Kia Rio", "compact", "Компактный", "2026-09-01", "15:00", "Мария", "+79990000002", "done"),
    ]
    for r in rows:
        con.execute(
            """INSERT INTO bookings (id, draft_id, user_id, service_id, service_title, car, car_class_id,
               car_class_title, visit_date, visit_time, name, phone, comment, estimate_rub, addons, status,
               consent, consent_at, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'комментарий', 1500, '["Опция"]', ?, 1, '2026-10-01T10:00:00+00:00',
               '2026-10-01T10:00:00+00:00', '2026-10-01T10:00:00+00:00')""", r)
        con.execute("INSERT INTO booking_status_log (booking_id, old_status, new_status, changed_by, changed_at)"
                    " VALUES (?, NULL, 'new', NULL, '2026-10-01T10:00:00+00:00')", (r[0],))
    con.execute("INSERT INTO booking_status_log (booking_id, old_status, new_status, changed_by, changed_at)"
                " VALUES (1, 'new', 'accepted', 900, '2026-10-02T10:00:00+00:00')")
    con.commit()
    con.close()


async def test_v1_database_migrates_without_data_loss(tmp_path):
    path = tmp_path / "prod.db"
    make_v1_db(path)

    db = Database(path)
    await db.connect()
    repo = Repository(db)
    try:
        async with db.conn.execute("PRAGMA user_version") as cur:
            assert (await cur.fetchone())[0] == 2
        bookings = await repo.iter_all_bookings()
        assert [b.id for b in bookings] == [1, 2, 3, 4]
        b1 = bookings[0]
        assert (b1.name, b1.phone, b1.status.value, b1.comment, b1.estimate_rub, b1.addons) == (
            "Иван", "+79990000001", "accepted", "комментарий", 1500, ("Опция",))
        assert b1.created_at == "2026-10-01T10:00:00+00:00"
        # история статусов не потерялась при пересборке таблицы
        assert len(await repo.status_history(1)) == 2
        # календарные поля заполнены для заявок с конкретным временем
        assert (b1.start_at, b1.end_at) == ("2026-10-20T12:00", "2026-10-20T13:00")
        assert bookings[1].start_at == "2026-10-22T10:00" and bookings[1].end_at == "2026-10-23T20:00"   # полировка: 2 дня
        assert bookings[2].start_at is None                                            # «любое время» — слота нет
        # автомобили созданы и связаны; «bmw  x5» и «BMW X5» одного клиента — одно авто
        async with db.conn.execute("SELECT user_id, title FROM cars ORDER BY id") as cur:
            cars = [tuple(r) for r in await cur.fetchall()]
        assert cars == [(10, "BMW X5"), (11, "Kia Rio")]
        assert bookings[0].car_id == bookings[1].car_id and bookings[2].car_id == bookings[3].car_id
        # внешние ключи целы, новые статусы допустимы
        async with db.conn.execute("PRAGMA foreign_key_check") as cur:
            assert await cur.fetchall() == []
        await db.conn.execute("UPDATE bookings SET status='cancelled' WHERE id=2")
        await db.conn.commit()
    finally:
        await db.close()


async def test_backup_created_before_migration_and_readable(tmp_path):
    path = tmp_path / "prod.db"
    make_v1_db(path)
    db = Database(path)
    await db.connect()
    await db.close()
    backups = list((tmp_path / "backups").glob("pre-migration-v1-to-v2-*.db"))
    assert len(backups) == 1
    con = sqlite3.connect(backups[0])
    assert con.execute("PRAGMA user_version").fetchone()[0] == 1          # копия — исходная версия
    assert con.execute("SELECT COUNT(*) FROM bookings").fetchone()[0] == 4
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    con.close()


async def test_second_start_does_not_migrate_or_backup_again(tmp_path):
    path = tmp_path / "prod.db"
    make_v1_db(path)
    for _ in range(3):
        db = Database(path)
        await db.connect()
        await db.close()
    assert len(list((tmp_path / "backups").glob("pre-migration-*.db"))) == 1


async def test_failed_migration_rolls_back_completely(tmp_path, monkeypatch):
    path = tmp_path / "prod.db"
    make_v1_db(path)

    async def boom(conn):
        await conn.execute("DROP TABLE bookings")          # уже что-то разрушили…
        raise RuntimeError("сбой посреди миграции")

    monkeypatch.setitem(migrations.MIGRATIONS, 2, boom)
    db = Database(path)
    with pytest.raises(RuntimeError, match="сбой"):
        await db.connect()

    con = sqlite3.connect(path)
    assert con.execute("PRAGMA user_version").fetchone()[0] == 1       # версия не изменилась
    assert con.execute("SELECT COUNT(*) FROM bookings").fetchone()[0] == 4   # данные на месте
    assert con.execute("SELECT COUNT(*) FROM booking_status_log").fetchone()[0] == 5
    con.close()


async def test_fresh_database_gets_latest_schema_without_backup(tmp_path):
    db = Database(tmp_path / "fresh.db")
    await db.connect()
    async with db.conn.execute("PRAGMA user_version") as cur:
        assert (await cur.fetchone())[0] == 2
    async with db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'") as cur:
        tables = {r[0] for r in await cur.fetchall()}
    assert {"cars", "jobs", "reviews", "vouchers", "discount_rules", "schedule_blocks"} <= tables
    await db.close()
    assert not (tmp_path / "backups").exists()


async def test_database_from_newer_version_is_refused(tmp_path):
    path = tmp_path / "future.db"
    con = sqlite3.connect(path)
    con.executescript(SCHEMA_V1)
    con.execute("PRAGMA user_version = 99")
    con.commit()
    con.close()
    db = Database(path)
    with pytest.raises(RuntimeError, match="более новой"):
        await db.connect()
