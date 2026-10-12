"""Резервные копии SQLite средствами самого бота (для хостингов без доступа к серверу).

Снимок делается командой VACUUM INTO — консистентно, пока бот работает. Копия проверяется
(`PRAGMA integrity_check`), сжимается и кладётся рядом с базой: <каталог базы>/backups/.
Хранятся последние N копий (по одной в сутки).
"""
from __future__ import annotations

import asyncio
import gzip
import logging
import shutil
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from database.repository import Repository

log = logging.getLogger(__name__)

BACKUP_GLOB = "detail_lab-*.db.gz"
_backup_lock = asyncio.Lock()  # ночная копия и ручной /backup не должны мешать друг другу


class BackupError(RuntimeError):
    pass


def _check_integrity(path: Path) -> None:
    con = sqlite3.connect(path)
    try:
        result = con.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        con.close()
    if result != "ok":
        raise BackupError(f"копия не прошла integrity_check: {result}")


def _compress(src: Path, dst: Path) -> None:
    part = dst.with_name(dst.name + ".part")
    with open(src, "rb") as fin, gzip.open(part, "wb") as fout:
        shutil.copyfileobj(fin, fout)
    part.replace(dst)  # атомарно: недописанный файл не станет «копией»


def rotate(backup_dir: Path, keep: int) -> list[Path]:
    """Оставляет `keep` самых новых копий (имена с датой сортируются по времени). Возвращает удалённые."""
    files = sorted(backup_dir.glob(BACKUP_GLOB), reverse=True)
    removed = files[keep:]
    for old in removed:
        old.unlink(missing_ok=True)
    return removed


async def make_backup(repo: Repository, backup_dir: Path, *, keep: int = 7,
                      now: datetime | None = None) -> Path:
    """Делает копию базы и возвращает путь к .db.gz. Бросает BackupError при неудаче."""
    if keep < 1:
        raise BackupError("keep должен быть >= 1")
    async with _backup_lock:
        try:
            backup_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise BackupError(f"не удалось создать папку для копий {backup_dir}: {exc}") from exc
        day = (now or datetime.now()).strftime("%Y-%m-%d")
        tmp = backup_dir / f".detail_lab-{day}-{uuid.uuid4().hex[:8]}.db.tmp"
        final = backup_dir / f"detail_lab-{day}.db.gz"
        try:
            await repo.snapshot_to(str(tmp))
            await asyncio.to_thread(_check_integrity, tmp)
            await asyncio.to_thread(_compress, tmp, final)
        except BackupError:
            raise
        except Exception as exc:
            raise BackupError(f"не удалось сделать копию: {exc}") from exc
        finally:
            for leftover in (tmp, Path(f"{tmp}-wal"), Path(f"{tmp}-shm")):
                leftover.unlink(missing_ok=True)
        rotate(backup_dir, keep)
        return final


def seconds_until(hour: int, minute: int, tz_name: str, *, now: datetime | None = None) -> float:
    """Сколько секунд до ближайшего hh:mm по часовому поясу студии."""
    tz = ZoneInfo(tz_name)
    current = (now or datetime.now(tz)).astimezone(tz)
    target = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= current:
        target += timedelta(days=1)
    return (target - current).total_seconds()
