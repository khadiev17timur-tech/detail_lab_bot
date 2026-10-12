"""Запросы к базе. Все SQL параметризованы."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from database.connection import Database
from database.models import (Booking, BookingStatus, NewBooking, Stats, StatusResult)

_BOOKING_SELECT = """
SELECT b.*, u.username AS username, u.full_name AS tg_full_name
FROM bookings b
LEFT JOIN users u ON u.tg_id = b.user_id
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Repository:
    def __init__(self, db: Database):
        self._db = db
        # Все записи выполняются под одним замком, чтобы транзакции корутин не переплетались.
        self._write_lock = asyncio.Lock()

    # ---------------------------------------------------------------- users
    async def upsert_user(self, tg_id: int, username: str | None, full_name: str | None) -> None:
        now = utcnow()
        async with self._write_lock:
            try:
                await self._db.conn.execute(
                    """
                    INSERT INTO users (tg_id, username, full_name, first_seen, last_seen)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(tg_id) DO UPDATE SET
                        username = excluded.username,
                        full_name = excluded.full_name,
                        last_seen = excluded.last_seen
                    """,
                    (tg_id, username, full_name, now, now),
                )
                await self._db.conn.commit()
            except Exception:
                await self._db.conn.rollback()
                raise

    # ------------------------------------------------------------- bookings
    async def create_booking(self, new: NewBooking) -> tuple[Booking, bool]:
        """Создаёт заявку. Возвращает (заявка, создана_ли_сейчас).

        Повторная отправка того же черновика (draft_id) не создаёт дубль:
        возвращается уже существующая заявка и created=False.
        """
        now = utcnow()
        conn = self._db.conn
        async with self._write_lock:
            try:
                cur = await conn.execute(
                    """
                    INSERT INTO bookings (
                        draft_id, user_id, service_id, service_title, car,
                        car_class_id, car_class_title, visit_date, visit_time,
                        name, phone, comment, estimate_rub, addons, status,
                        consent, consent_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?, ?, ?)
                    ON CONFLICT(draft_id) DO NOTHING
                    """,
                    (
                        new.draft_id, new.user_id, new.service_id, new.service_title, new.car,
                        new.car_class_id, new.car_class_title, new.visit_date, new.visit_time,
                        new.name, new.phone, new.comment, new.estimate_rub,
                        json.dumps(list(new.addons), ensure_ascii=False),
                        1 if new.consent else 0, now if new.consent else None, now, now,
                    ),
                )
                created = cur.rowcount == 1
                booking_id = cur.lastrowid
                if created:
                    await conn.execute(
                        "INSERT INTO booking_status_log (booking_id, old_status, new_status, changed_by, changed_at)"
                        " VALUES (?, NULL, 'new', NULL, ?)",
                        (booking_id, now),
                    )
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise
        if created:
            booking = await self.get_booking(booking_id)
        else:
            booking = await self.get_booking_by_draft(new.draft_id)
        assert booking is not None
        return booking, created

    async def get_booking(self, booking_id: int) -> Booking | None:
        async with self._db.conn.execute(_BOOKING_SELECT + " WHERE b.id = ?", (booking_id,)) as cur:
            row = await cur.fetchone()
        return Booking.from_row(row) if row else None

    async def get_booking_by_draft(self, draft_id: str) -> Booking | None:
        async with self._db.conn.execute(_BOOKING_SELECT + " WHERE b.draft_id = ?", (draft_id,)) as cur:
            row = await cur.fetchone()
        return Booking.from_row(row) if row else None

    async def list_bookings(self, status: BookingStatus | None = None, *,
                            limit: int = 10, offset: int = 0) -> list[Booking]:
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        sql = _BOOKING_SELECT
        params: list = []
        if status is not None:
            sql += " WHERE b.status = ?"
            params.append(status.value)
        sql += " ORDER BY b.id DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        async with self._db.conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [Booking.from_row(r) for r in rows]

    async def iter_all_bookings(self) -> list[Booking]:
        async with self._db.conn.execute(_BOOKING_SELECT + " ORDER BY b.id ASC") as cur:
            rows = await cur.fetchall()
        return [Booking.from_row(r) for r in rows]

    async def count_bookings(self, status: BookingStatus | None = None) -> int:
        if status is None:
            sql, params = "SELECT COUNT(*) FROM bookings", ()
        else:
            sql, params = "SELECT COUNT(*) FROM bookings WHERE status = ?", (status.value,)
        async with self._db.conn.execute(sql, params) as cur:
            row = await cur.fetchone()
        return int(row[0])

    # ---------------------------------------------------------------- status
    async def set_status(self, booking_id: int, new_status: BookingStatus, *,
                         actor_id: int | None,
                         expected: BookingStatus | None = None) -> StatusResult:
        """Атомарно меняет статус (compare-and-set).

        changed=False, если заявки нет, статус уже такой или не совпал `expected`
        (кто-то обработал заявку раньше) — так повторные нажатия безопасны.
        """
        conn = self._db.conn
        async with self._write_lock:
            try:
                async with conn.execute("SELECT status FROM bookings WHERE id = ?", (booking_id,)) as cur:
                    row = await cur.fetchone()
                if row is None:
                    return StatusResult(None, False)
                current = BookingStatus(row["status"])
                if current == new_status or (expected is not None and current != expected):
                    changed = False
                else:
                    now = utcnow()
                    cur2 = await conn.execute(
                        "UPDATE bookings SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
                        (new_status.value, now, booking_id, current.value),
                    )
                    changed = cur2.rowcount == 1
                    if changed:
                        await conn.execute(
                            "INSERT INTO booking_status_log (booking_id, old_status, new_status, changed_by, changed_at)"
                            " VALUES (?, ?, ?, ?, ?)",
                            (booking_id, current.value, new_status.value, actor_id, now),
                        )
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise
        booking = await self.get_booking(booking_id)
        return StatusResult(booking, changed, current)

    async def status_history(self, booking_id: int) -> list[tuple[str | None, str, int | None, str]]:
        async with self._db.conn.execute(
            "SELECT old_status, new_status, changed_by, changed_at FROM booking_status_log"
            " WHERE booking_id = ? ORDER BY id ASC", (booking_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [(r["old_status"], r["new_status"], r["changed_by"], r["changed_at"]) for r in rows]

    # ----------------------------------------------------------------- stats
    async def stats(self) -> Stats:
        conn = self._db.conn
        async with conn.execute("SELECT status, COUNT(*) AS n FROM bookings GROUP BY status") as cur:
            by_status = {BookingStatus(r["status"]): int(r["n"]) for r in await cur.fetchall()}
        async with conn.execute("SELECT COUNT(*) FROM users") as cur:
            users = int((await cur.fetchone())[0])
        week_ago = (datetime.now(timezone.utc).timestamp() - 7 * 86400)
        week_iso = datetime.fromtimestamp(week_ago, timezone.utc).isoformat(timespec="seconds")
        async with conn.execute("SELECT COUNT(*) FROM bookings WHERE created_at >= ?", (week_iso,)) as cur:
            last_7 = int((await cur.fetchone())[0])
        async with conn.execute(
            "SELECT service_title, COUNT(*) AS n FROM bookings GROUP BY service_id, service_title"
            " ORDER BY n DESC, service_title ASC LIMIT 3"
        ) as cur:
            top = [(r["service_title"], int(r["n"])) for r in await cur.fetchall()]
        return Stats(total=sum(by_status.values()), by_status=by_status,
                     users=users, last_7_days=last_7, top_services=top)
