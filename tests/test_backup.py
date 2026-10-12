import gzip
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio

from database import Database, NewBooking, Repository
from services.admin import AdminService, PermissionDenied
from services.backup import BackupError, make_backup, rotate, seconds_until
from tests.conftest import ADMIN_ID, CLIENT_ID

pytestmark = pytest.mark.asyncio


def booking(i):
    return NewBooking(draft_id=f"d{i}", user_id=1, service_id="wash", service_title="Мойка", car="Kia Rio",
                      car_class_id="compact", car_class_title="Компактный", visit_date="2030-01-01",
                      visit_time="12:00", name=f"Клиент {i}", phone="+79990000000", consent=True)


@pytest_asyncio.fixture
async def repo(tmp_path):
    db = Database(tmp_path / "data" / "db.sqlite")
    await db.connect()
    r = Repository(db)
    await r.upsert_user(1, "u", "U")
    for i in range(30):
        await r.create_booking(booking(i))
    yield r
    await db.close()


def read_copy(path, tmp_path):
    out = tmp_path / "restored.db"
    out.write_bytes(gzip.decompress(path.read_bytes()))
    return sqlite3.connect(out)


async def test_backup_is_valid_and_complete(repo, tmp_path):
    path = await make_backup(repo, tmp_path / "bk", now=datetime(2026, 10, 12))
    assert path.name == "detail_lab-2026-10-12.db.gz"
    con = read_copy(path, tmp_path)
    assert con.execute("SELECT COUNT(*) FROM bookings").fetchone()[0] == 30
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert [p.name for p in (tmp_path / "bk").iterdir()] == [path.name]   # временных файлов не осталось


async def test_backup_during_writes_stays_consistent(repo, tmp_path):
    import asyncio

    async def writer():
        for i in range(100, 200):
            await repo.create_booking(booking(i))

    results = await asyncio.gather(writer(), make_backup(repo, tmp_path / "bk"), make_backup(repo, tmp_path / "bk"))
    con = read_copy(results[1], tmp_path)
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert 30 <= con.execute("SELECT COUNT(*) FROM bookings").fetchone()[0] <= 130


async def test_rotation_keeps_newest_only(repo, tmp_path):
    bk = tmp_path / "bk"
    bk.mkdir()
    for d in range(1, 13):
        (bk / f"detail_lab-2026-09-{d:02d}.db.gz").write_bytes(b"x")
    (bk / "readme.txt").write_text("не трогать")
    await make_backup(repo, bk, keep=7, now=datetime(2026, 10, 12))
    names = sorted(p.name for p in bk.glob("detail_lab-*.db.gz"))
    assert len(names) == 7 and names[-1] == "detail_lab-2026-10-12.db.gz" and names[0] == "detail_lab-2026-09-07.db.gz"
    assert (bk / "readme.txt").exists()
    assert rotate(bk, 7) == []


async def test_same_day_backup_replaces_not_duplicates(repo, tmp_path):
    await make_backup(repo, tmp_path / "bk", now=datetime(2026, 10, 12))
    await repo.create_booking(booking(999))
    await make_backup(repo, tmp_path / "bk", now=datetime(2026, 10, 12))
    files = list((tmp_path / "bk").glob("detail_lab-*.db.gz"))
    assert len(files) == 1
    assert read_copy(files[0], tmp_path).execute("SELECT COUNT(*) FROM bookings").fetchone()[0] == 31


async def test_backup_errors_are_reported(repo, tmp_path):
    with pytest.raises(BackupError):
        await make_backup(repo, tmp_path / "bk", keep=0)
    blocker = tmp_path / "file_not_dir"
    blocker.write_text("x")
    with pytest.raises(BackupError):
        await make_backup(repo, blocker / "bk")        # нельзя создать каталог внутри файла


async def test_strangers_cannot_backup(repo, tmp_path):
    admin = AdminService(repo, {ADMIN_ID})
    with pytest.raises(PermissionDenied):
        await admin.backup(CLIENT_ID, tmp_path / "bk")
    with pytest.raises(PermissionDenied):
        await admin.backup(None, tmp_path / "bk")
    assert not (tmp_path / "bk").exists()
    assert (await admin.backup(ADMIN_ID, tmp_path / "bk")).exists()


async def test_scheduler_next_run_time():
    tz = ZoneInfo("Europe/Moscow")
    assert seconds_until(3, 30, "Europe/Moscow", now=datetime(2026, 10, 12, 3, 0, tzinfo=tz)) == 30 * 60
    assert seconds_until(3, 30, "Europe/Moscow", now=datetime(2026, 10, 12, 3, 30, tzinfo=tz)) == 24 * 3600
    assert seconds_until(3, 30, "Europe/Moscow", now=datetime(2026, 10, 12, 23, 30, tzinfo=tz)) == 4 * 3600


# ------------------------------------------------ через бота (сценарные)
async def test_backup_command_for_admin_sends_file_and_denies_others(env):
    await env.repo.upsert_user(CLIENT_ID, "ivan", "Иван")
    await env.send(CLIENT_ID, "/backup")
    assert "нет доступа" in env.last_text(CLIENT_ID)
    assert not [o for o in env.tg.outbox if o.kind == "SendDocument"]

    await env.send(ADMIN_ID, "/backup")
    docs = [o for o in env.tg.outbox if o.kind == "SendDocument"]
    assert len(docs) == 1 and docs[0].chat_id == ADMIN_ID and docs[0].filename.endswith(".db.gz")
    assert "Резервная копия" in docs[0].text


async def test_backup_button_in_admin_panel(env):
    await env.send(ADMIN_ID, "/admin")
    await env.click(ADMIN_ID, "Бэкап базы")
    assert [o for o in env.tg.outbox if o.kind == "SendDocument"]
