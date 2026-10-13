"""Запросы к базе. Все SQL параметризованы; записи выполняются под одним замком и в транзакциях."""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Sequence

from database.connection import Database
from database.errors import NotAllowed, SlotTaken, VoucherUnavailable
from database.migrations import car_key
from database.models import (ACTIVE_STATUSES, Booking, BookingStatus, Car, DiscountRule, Job, NewBooking,
                             Review, ScheduleBlock, Stats, StatusResult, UserRow, Voucher)
from services.schedule import from_iso, has_capacity, to_iso

_BOOKING_SELECT = """
SELECT b.*, u.username AS username, u.full_name AS tg_full_name
FROM bookings b
LEFT JOIN users u ON u.tg_id = b.user_id
"""
_ACTIVE_SQL = "(" + ",".join(f"'{s.value}'" for s in ACTIVE_STATUSES) + ")"

JobSpec = tuple[str, str]   # (kind, due_at в UTC ISO)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


class Repository:
    def __init__(self, db: Database):
        self._db = db
        # Все записи выполняются под одним замком, чтобы транзакции корутин не переплетались.
        self._write_lock = asyncio.Lock()

    # ---------------------------------------------------------------- helpers
    @property
    def _conn(self):
        return self._db.conn

    async def _fetchone(self, sql: str, params: Sequence = ()):
        async with self._conn.execute(sql, params) as cur:
            return await cur.fetchone()

    async def _fetchall(self, sql: str, params: Sequence = ()):
        async with self._conn.execute(sql, params) as cur:
            return await cur.fetchall()

    async def _busy_tx(self, start: datetime, end: datetime, exclude_id: int | None = None) -> list[tuple[datetime, datetime]]:
        """Занятые интервалы активных записей, пересекающие окно [start, end)."""
        rows = await self._fetchall(
            f"SELECT start_at, end_at FROM bookings WHERE status IN {_ACTIVE_SQL} AND start_at IS NOT NULL "
            "AND end_at > ? AND start_at < ? AND (? IS NULL OR id <> ?)",
            (to_iso(start), to_iso(end), exclude_id, exclude_id))
        return [(from_iso(r["start_at"]), from_iso(r["end_at"])) for r in rows]

    async def _ensure_car_tx(self, user_id: int, title: str, class_id: str | None, now: str) -> int | None:
        key = car_key(title)
        if not key:
            return None
        await self._conn.execute(
            "INSERT OR IGNORE INTO cars (user_id, title, title_key, car_class_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, title.strip(), key, class_id, now))
        row = await self._fetchone("SELECT id, archived FROM cars WHERE user_id = ? AND title_key = ?", (user_id, key))
        if row["archived"]:
            await self._conn.execute("UPDATE cars SET archived = 0 WHERE id = ?", (row["id"],))
        return row["id"]

    async def _insert_jobs_tx(self, booking_id: int, user_id: int, jobs: Iterable[JobSpec], now: str) -> None:
        for kind, due in jobs:
            await self._conn.execute(
                "INSERT OR IGNORE INTO jobs (kind, booking_id, user_id, due_at, status, created_at)"
                " VALUES (?, ?, ?, ?, 'pending', ?)", (kind, booking_id, user_id, due, now))

    async def _release_voucher_tx(self, booking_id: int, now: str) -> None:
        """Возвращает зарезервированный купон клиенту (срок действия сохраняется прежним)."""
        await self._conn.execute(
            "UPDATE vouchers SET status = 'active', booking_id = NULL WHERE booking_id = ? AND status = 'reserved'",
            (booking_id,))

    # ---------------------------------------------------------------- backup
    async def snapshot_to(self, path: str) -> None:
        """Консистентный снимок базы в отдельный файл (VACUUM INTO) — безопасно при работающем боте."""
        async with self._write_lock:
            await self._conn.commit()
            await self._conn.execute("VACUUM INTO ?", (str(path),))

    # ---------------------------------------------------------------- users
    async def upsert_user(self, tg_id: int, username: str | None, full_name: str | None) -> None:
        now = utcnow()
        async with self._write_lock:
            try:
                await self._conn.execute(
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
                await self._conn.commit()
            except Exception:
                await self._conn.rollback()
                raise

    async def get_user(self, tg_id: int) -> UserRow | None:
        r = await self._fetchone("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
        if r is None:
            return None
        return UserRow(r["tg_id"], r["username"], r["full_name"], bool(r["marketing_consent"]), bool(r["marketing_asked"]))

    async def set_marketing(self, tg_id: int, enabled: bool) -> None:
        now = utcnow()
        async with self._write_lock:
            await self._conn.execute(
                "UPDATE users SET marketing_consent = ?, marketing_consent_at = ?, marketing_asked = 1 WHERE tg_id = ?",
                (1 if enabled else 0, now if enabled else None, tg_id))
            await self._conn.commit()

    async def mark_marketing_asked(self, tg_id: int) -> None:
        async with self._write_lock:
            await self._conn.execute("UPDATE users SET marketing_asked = 1 WHERE tg_id = ?", (tg_id,))
            await self._conn.commit()

    # ------------------------------------------------------------- bookings
    async def create_booking(self, new: NewBooking, *, posts: int | None = None,
                             jobs: Iterable[JobSpec] = ()) -> tuple[Booking, bool]:
        """Создаёт заявку. Возвращает (заявка, создана_ли_сейчас).

        * Повторная отправка того же черновика (draft_id) не создаёт дубль.
        * Если задан слот (start_at/end_at) и `posts`, занятость проверяется ВНУТРИ транзакции:
          два клиента не могут занять последний пост — второй получит SlotTaken.
        * Купон (voucher_id) резервируется в той же транзакции; недоступный — VoucherUnavailable.
        """
        now = utcnow()
        conn = self._conn
        async with self._write_lock:
            try:
                existing = await self._fetchone("SELECT id FROM bookings WHERE draft_id = ?", (new.draft_id,))
                if existing is not None:
                    booking_id, created = existing["id"], False
                else:
                    if new.start_at and new.end_at and posts is not None:
                        start, end = from_iso(new.start_at), from_iso(new.end_at)
                        if not has_capacity(await self._busy_tx(start, end), start, end, posts):
                            raise SlotTaken(new.start_at)
                    if new.voucher_id is not None:
                        v = await self._fetchone(
                            "SELECT id FROM vouchers WHERE id = ? AND user_id = ? AND status = 'active' AND expires_at > ?",
                            (new.voucher_id, new.user_id, now))
                        if v is None:
                            raise VoucherUnavailable(new.voucher_id)
                    car_id = await self._ensure_car_tx(new.user_id, new.car, new.car_class_id, now)
                    cur = await conn.execute(
                        """
                        INSERT INTO bookings (
                            draft_id, user_id, service_id, service_title, car,
                            car_class_id, car_class_title, visit_date, visit_time,
                            name, phone, comment, estimate_rub, addons, status,
                            consent, consent_at, created_at, updated_at,
                            start_at, end_at, car_id, voucher_id, discount_rub
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            new.draft_id, new.user_id, new.service_id, new.service_title, new.car,
                            new.car_class_id, new.car_class_title, new.visit_date, new.visit_time,
                            new.name, new.phone, new.comment, new.estimate_rub,
                            json.dumps(list(new.addons), ensure_ascii=False),
                            1 if new.consent else 0, now if new.consent else None, now, now,
                            new.start_at, new.end_at, car_id, new.voucher_id, new.discount_rub,
                        ),
                    )
                    booking_id, created = cur.lastrowid, True
                    await conn.execute(
                        "INSERT INTO booking_status_log (booking_id, old_status, new_status, changed_by, changed_at)"
                        " VALUES (?, NULL, 'new', NULL, ?)", (booking_id, now))
                    if new.voucher_id is not None:
                        await conn.execute(
                            "UPDATE vouchers SET status = 'reserved', booking_id = ? WHERE id = ?",
                            (booking_id, new.voucher_id))
                    await self._insert_jobs_tx(booking_id, new.user_id, jobs, now)
                await conn.commit()
            except BaseException:
                await conn.rollback()
                raise
        booking = await self.get_booking(booking_id)
        assert booking is not None
        return booking, created

    async def get_booking(self, booking_id: int) -> Booking | None:
        row = await self._fetchone(_BOOKING_SELECT + " WHERE b.id = ?", (booking_id,))
        return Booking.from_row(row) if row else None

    async def get_booking_by_draft(self, draft_id: str) -> Booking | None:
        row = await self._fetchone(_BOOKING_SELECT + " WHERE b.draft_id = ?", (draft_id,))
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
        return [Booking.from_row(r) for r in await self._fetchall(sql, params)]

    async def iter_all_bookings(self) -> list[Booking]:
        return [Booking.from_row(r) for r in await self._fetchall(_BOOKING_SELECT + " ORDER BY b.id ASC")]

    async def count_bookings(self, status: BookingStatus | None = None) -> int:
        if status is None:
            row = await self._fetchone("SELECT COUNT(*) FROM bookings")
        else:
            row = await self._fetchone("SELECT COUNT(*) FROM bookings WHERE status = ?", (status.value,))
        return int(row[0])

    async def busy_intervals(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        return await self._busy_tx(start, end)

    async def bookings_on_day(self, day: date) -> list[Booking]:
        rows = await self._fetchall(
            _BOOKING_SELECT + f" WHERE b.status IN {_ACTIVE_SQL} AND ((b.start_at >= ? AND b.start_at < ?)"
            " OR (b.start_at IS NULL AND b.visit_date = ?)) ORDER BY b.start_at, b.id",
            (f"{day.isoformat()}T00:00", f"{(day + timedelta(days=1)).isoformat()}T00:00", day.isoformat()))
        return [Booking.from_row(r) for r in rows]

    async def upcoming_for_user(self, user_id: int, now_local: datetime) -> list[Booking]:
        """Будущие активные записи клиента (включая ещё идущие многодневные услуги)."""
        rows = await self._fetchall(
            _BOOKING_SELECT + f" WHERE b.user_id = ? AND b.status IN {_ACTIVE_SQL} AND "
            "((b.end_at IS NOT NULL AND b.end_at >= ?) OR (b.end_at IS NULL AND b.visit_date >= ?))"
            " ORDER BY COALESCE(b.start_at, b.visit_date), b.id",
            (user_id, to_iso(now_local), now_local.date().isoformat()))
        return [Booking.from_row(r) for r in rows]

    async def done_for_user(self, user_id: int, car_id: int | None = None, limit: int = 20) -> list[Booking]:
        sql = _BOOKING_SELECT + " WHERE b.user_id = ? AND b.status = 'done'"
        params: list = [user_id]
        if car_id is not None:
            sql += " AND b.car_id = ?"
            params.append(car_id)
        sql += " ORDER BY b.visit_date DESC, b.id DESC LIMIT ?"
        params.append(limit)
        return [Booking.from_row(r) for r in await self._fetchall(sql, params)]

    # ---------------------------------------------------------------- status
    async def set_status(self, booking_id: int, new_status: BookingStatus, *,
                         actor_id: int | None, expected: BookingStatus | None = None,
                         cancelled_by: str | None = None, jobs: Iterable[JobSpec] = ()) -> StatusResult:
        """Атомарно меняет статус (compare-and-set) вместе со всеми последствиями:

        * отказ/отмена — освобождают слот (он считается занятым только у активных статусов),
          возвращают зарезервированный купон и отменяют ожидающие задания;
        * завершение — «гасит» купон и ставит в очередь задания (отзыв, рекомендации).

        changed=False, если заявки нет, статус уже такой или не совпал `expected`.
        """
        conn = self._conn
        async with self._write_lock:
            try:
                row = await self._fetchone("SELECT status, user_id FROM bookings WHERE id = ?", (booking_id,))
                if row is None:
                    return StatusResult(None, False)
                current = BookingStatus(row["status"])
                if current == new_status or (expected is not None and current != expected):
                    changed = False
                else:
                    now = utcnow()
                    cur2 = await conn.execute(
                        "UPDATE bookings SET status = ?, updated_at = ?, cancelled_by = COALESCE(?, cancelled_by)"
                        " WHERE id = ? AND status = ?",
                        (new_status.value, now, cancelled_by, booking_id, current.value))
                    changed = cur2.rowcount == 1
                    if changed:
                        await conn.execute(
                            "INSERT INTO booking_status_log (booking_id, old_status, new_status, changed_by, changed_at)"
                            " VALUES (?, ?, ?, ?, ?)", (booking_id, current.value, new_status.value, actor_id, now))
                        if new_status in (BookingStatus.REJECTED, BookingStatus.CANCELLED):
                            await self._release_voucher_tx(booking_id, now)
                            await conn.execute(
                                "UPDATE jobs SET status = 'cancelled' WHERE booking_id = ? AND status = 'pending'",
                                (booking_id,))
                        elif new_status == BookingStatus.DONE:
                            await conn.execute(
                                "UPDATE vouchers SET status = 'used', used_at = ? WHERE booking_id = ? AND status = 'reserved'",
                                (now, booking_id))
                            await conn.execute(
                                "UPDATE jobs SET status = 'cancelled' WHERE booking_id = ? AND status = 'pending' AND kind LIKE 'remind:%'",
                                (booking_id,))
                        await self._insert_jobs_tx(booking_id, row["user_id"], jobs, now)
                await conn.commit()
            except BaseException:
                await conn.rollback()
                raise
        booking = await self.get_booking(booking_id)
        return StatusResult(booking, changed, current)

    async def reschedule(self, booking_id: int, user_id: int, new_start: datetime, new_end: datetime, *,
                         posts: int, jobs: Iterable[JobSpec] = ()) -> Booking:
        """Переносит запись клиента на новое время. Запись снова ждёт подтверждения менеджера.

        Занятость проверяется в транзакции (свой старый интервал не считается занятым).
        """
        conn = self._conn
        async with self._write_lock:
            try:
                row = await self._fetchone("SELECT * FROM bookings WHERE id = ? AND user_id = ?", (booking_id, user_id))
                if row is None or BookingStatus(row["status"]) not in ACTIVE_STATUSES:
                    raise NotAllowed("перенос недоступен для этой записи")
                busy = await self._busy_tx(new_start, new_end, exclude_id=booking_id)
                if not has_capacity(busy, new_start, new_end, posts):
                    raise SlotTaken(to_iso(new_start))
                now = utcnow()
                await conn.execute(
                    "UPDATE bookings SET start_at = ?, end_at = ?, visit_date = ?, visit_time = ?, status = 'new',"
                    " client_confirmed_at = NULL, reschedule_count = reschedule_count + 1, updated_at = ? WHERE id = ?",
                    (to_iso(new_start), to_iso(new_end), new_start.date().isoformat(), new_start.strftime("%H:%M"),
                     now, booking_id))
                if row["status"] != "new":
                    await conn.execute(
                        "INSERT INTO booking_status_log (booking_id, old_status, new_status, changed_by, changed_at)"
                        " VALUES (?, ?, 'new', ?, ?)", (booking_id, row["status"], user_id, now))
                await conn.execute(
                    "UPDATE jobs SET status = 'cancelled' WHERE booking_id = ? AND status = 'pending' AND kind LIKE 'remind:%'",
                    (booking_id,))
                await conn.execute("DELETE FROM jobs WHERE booking_id = ? AND kind LIKE 'remind:%' AND status = 'cancelled'",
                                   (booking_id,))
                await self._insert_jobs_tx(booking_id, user_id, jobs, now)
                await conn.commit()
            except BaseException:
                await conn.rollback()
                raise
        booking = await self.get_booking(booking_id)
        assert booking is not None
        return booking

    async def confirm_attendance(self, booking_id: int, user_id: int) -> bool:
        """Клиент подтвердил, что придёт. Идемпотентно: True только при первом подтверждении."""
        async with self._write_lock:
            cur = await self._conn.execute(
                f"UPDATE bookings SET client_confirmed_at = ? WHERE id = ? AND user_id = ? AND client_confirmed_at IS NULL"
                f" AND status IN {_ACTIVE_SQL}", (utcnow(), booking_id, user_id))
            await self._conn.commit()
            return cur.rowcount == 1

    async def status_history(self, booking_id: int) -> list[tuple[str | None, str, int | None, str]]:
        rows = await self._fetchall(
            "SELECT old_status, new_status, changed_by, changed_at FROM booking_status_log"
            " WHERE booking_id = ? ORDER BY id ASC", (booking_id,))
        return [(r["old_status"], r["new_status"], r["changed_by"], r["changed_at"]) for r in rows]

    # ------------------------------------------------------------------ cars
    async def list_cars(self, user_id: int) -> list[Car]:
        rows = await self._fetchall(
            "SELECT * FROM cars WHERE user_id = ? AND archived = 0 ORDER BY id", (user_id,))
        return [Car(r["id"], r["user_id"], r["title"], r["car_class_id"], r["created_at"]) for r in rows]

    async def get_car(self, car_id: int, user_id: int) -> Car | None:
        r = await self._fetchone("SELECT * FROM cars WHERE id = ? AND user_id = ? AND archived = 0", (car_id, user_id))
        return Car(r["id"], r["user_id"], r["title"], r["car_class_id"], r["created_at"]) if r else None

    async def add_car(self, user_id: int, title: str, class_id: str | None) -> Car | None:
        async with self._write_lock:
            car_id = await self._ensure_car_tx(user_id, title, class_id, utcnow())
            await self._conn.commit()
        return await self.get_car(car_id, user_id) if car_id else None

    async def archive_car(self, car_id: int, user_id: int) -> bool:
        async with self._write_lock:
            cur = await self._conn.execute(
                "UPDATE cars SET archived = 1 WHERE id = ? AND user_id = ? AND archived = 0", (car_id, user_id))
            await self._conn.commit()
            return cur.rowcount == 1

    # ------------------------------------------------------------------ jobs
    async def claim_due_jobs(self, now_iso: str, limit: int = 20) -> list[Job]:
        """Атомарно берёт готовые задания (pending→sending): два воркера не получат одно и то же."""
        async with self._write_lock:
            rows = await self._fetchall(
                "SELECT * FROM jobs WHERE status = 'pending' AND due_at <= ? ORDER BY due_at, id LIMIT ?",
                (now_iso, limit))
            claimed = []
            for r in rows:
                cur = await self._conn.execute(
                    "UPDATE jobs SET status = 'sending', attempts = attempts + 1 WHERE id = ? AND status = 'pending'",
                    (r["id"],))
                if cur.rowcount == 1:
                    claimed.append(Job(r["id"], r["kind"], r["booking_id"], r["user_id"], r["due_at"],
                                       "sending", r["attempts"] + 1))
            await self._conn.commit()
            return claimed

    async def finish_job(self, job_id: int, status: str, note: str | None = None) -> None:
        assert status in {"sent", "skipped", "failed", "cancelled"}
        async with self._write_lock:
            await self._conn.execute("UPDATE jobs SET status = ?, note = ?, sent_at = ? WHERE id = ?",
                                     (status, note, utcnow() if status == "sent" else None, job_id))
            await self._conn.commit()

    async def retry_job_later(self, job_id: int, due_iso: str, note: str | None = None) -> None:
        async with self._write_lock:
            await self._conn.execute("UPDATE jobs SET status = 'pending', due_at = ?, note = ? WHERE id = ?",
                                     (due_iso, note, job_id))
            await self._conn.commit()

    async def recover_stuck_jobs(self) -> int:
        """После падения процесса задания в статусе sending могли быть отправлены, а могли нет.
        Чтобы не слать дубль клиенту, помечаем их failed (на «потерянных» это безопаснее повторной отправки)."""
        async with self._write_lock:
            cur = await self._conn.execute(
                "UPDATE jobs SET status = 'failed', note = 'прервано перезапуском' WHERE status = 'sending'")
            await self._conn.commit()
            return cur.rowcount

    async def jobs_for_booking(self, booking_id: int) -> list[Job]:
        rows = await self._fetchall("SELECT * FROM jobs WHERE booking_id = ? ORDER BY due_at, id", (booking_id,))
        return [Job(r["id"], r["kind"], r["booking_id"], r["user_id"], r["due_at"], r["status"], r["attempts"]) for r in rows]

    async def add_jobs(self, booking_id: int, user_id: int, jobs: Iterable[JobSpec]) -> None:
        async with self._write_lock:
            await self._insert_jobs_tx(booking_id, user_id, jobs, utcnow())
            await self._conn.commit()

    # --------------------------------------------------------------- reviews
    async def create_review(self, booking_id: int, user_id: int, rating: int, comment: str = "") -> Review | None:
        """Один отзыв на визит: повторная оценка не создаёт новую запись (возвращает None)."""
        if not 1 <= rating <= 5:
            raise ValueError("rating must be 1..5")
        async with self._write_lock:
            ok = await self._fetchone(
                "SELECT 1 FROM bookings WHERE id = ? AND user_id = ? AND status = 'done'", (booking_id, user_id))
            if ok is None:
                return None
            cur = await self._conn.execute(
                "INSERT OR IGNORE INTO reviews (booking_id, user_id, rating, comment, created_at) VALUES (?, ?, ?, ?, ?)",
                (booking_id, user_id, rating, comment.strip(), utcnow()))
            await self._conn.commit()
            if cur.rowcount != 1:
                return None
        return await self.get_review(booking_id)

    async def add_review_comment(self, booking_id: int, user_id: int, comment: str) -> bool:
        async with self._write_lock:
            cur = await self._conn.execute(
                "UPDATE reviews SET comment = ? WHERE booking_id = ? AND user_id = ? AND comment = ''",
                (comment.strip(), booking_id, user_id))
            await self._conn.commit()
            return cur.rowcount == 1

    async def get_review(self, booking_id: int) -> Review | None:
        r = await self._fetchone("SELECT * FROM reviews WHERE booking_id = ?", (booking_id,))
        return Review(r["id"], r["booking_id"], r["user_id"], r["rating"], r["comment"], r["created_at"]) if r else None

    async def recent_reviews(self, limit: int = 10) -> list[Review]:
        rows = await self._fetchall("SELECT * FROM reviews ORDER BY id DESC LIMIT ?", (limit,))
        return [Review(r["id"], r["booking_id"], r["user_id"], r["rating"], r["comment"], r["created_at"]) for r in rows]

    async def review_summary(self) -> tuple[int, float | None]:
        r = await self._fetchone("SELECT COUNT(*), AVG(rating) FROM reviews")
        return int(r[0]), (float(r[1]) if r[1] is not None else None)

    # ------------------------------------------------------------ discounts
    async def create_rule(self, *, title: str, kind: str, value: int, trigger: str, threshold: int,
                          valid_days: int, per_client_limit: int = 1, min_price: int = 0) -> DiscountRule:
        async with self._write_lock:
            cur = await self._conn.execute(
                "INSERT INTO discount_rules (title, kind, value, trigger, threshold, valid_days, per_client_limit,"
                " min_price, active, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                (title, kind, value, trigger, threshold, valid_days, per_client_limit, min_price, utcnow()))
            await self._conn.commit()
            rule_id = cur.lastrowid
        return await self.get_rule(rule_id)  # type: ignore[return-value]

    @staticmethod
    def _rule(r) -> DiscountRule:
        return DiscountRule(r["id"], r["title"], r["kind"], r["value"], r["trigger"], r["threshold"],
                            r["valid_days"], r["per_client_limit"], r["min_price"], bool(r["active"]))

    async def get_rule(self, rule_id: int) -> DiscountRule | None:
        r = await self._fetchone("SELECT * FROM discount_rules WHERE id = ?", (rule_id,))
        return self._rule(r) if r else None

    async def list_rules(self, active_only: bool = False) -> list[DiscountRule]:
        rows = await self._fetchall(
            "SELECT * FROM discount_rules" + (" WHERE active = 1" if active_only else "") + " ORDER BY id")
        return [self._rule(r) for r in rows]

    async def set_rule_active(self, rule_id: int, active: bool) -> bool:
        async with self._write_lock:
            cur = await self._conn.execute("UPDATE discount_rules SET active = ? WHERE id = ?", (1 if active else 0, rule_id))
            await self._conn.commit()
            return cur.rowcount == 1

    @staticmethod
    def _voucher(r) -> Voucher:
        return Voucher(r["id"], r["rule_id"], r["user_id"], r["title"], r["kind"], r["value"], r["min_price"],
                       r["issued_at"], r["expires_at"], r["status"], r["booking_id"])

    async def issue_voucher(self, rule: DiscountRule, user_id: int, now: datetime) -> Voucher | None:
        """Выдаёт купон по правилу, если лимит на клиента не исчерпан (проверка и вставка — атомарно)."""
        async with self._write_lock:
            issued = await self._fetchone(
                "SELECT COUNT(*) FROM vouchers WHERE rule_id = ? AND user_id = ? AND status <> 'revoked'",
                (rule.id, user_id))
            if issued[0] >= rule.per_client_limit:
                return None
            expires = iso_utc(now + timedelta(days=rule.valid_days))
            cur = await self._conn.execute(
                "INSERT INTO vouchers (rule_id, user_id, title, kind, value, min_price, issued_at, expires_at, status)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active')",
                (rule.id, user_id, rule.title, rule.kind, rule.value, rule.min_price, iso_utc(now), expires))
            await self._conn.commit()
            vid = cur.lastrowid
        return await self.get_voucher(vid)

    async def get_voucher(self, voucher_id: int) -> Voucher | None:
        r = await self._fetchone("SELECT * FROM vouchers WHERE id = ?", (voucher_id,))
        return self._voucher(r) if r else None

    async def active_vouchers(self, user_id: int, now: datetime) -> list[Voucher]:
        rows = await self._fetchall(
            "SELECT * FROM vouchers WHERE user_id = ? AND status = 'active' AND expires_at > ? ORDER BY expires_at",
            (user_id, iso_utc(now)))
        return [self._voucher(r) for r in rows]

    async def all_vouchers(self, user_id: int) -> list[Voucher]:
        rows = await self._fetchall("SELECT * FROM vouchers WHERE user_id = ? ORDER BY id DESC LIMIT 20", (user_id,))
        return [self._voucher(r) for r in rows]

    async def done_visit_count(self, user_id: int) -> int:
        return int((await self._fetchone("SELECT COUNT(*) FROM bookings WHERE user_id = ? AND status = 'done'", (user_id,)))[0])

    async def winback_candidates(self, rule: DiscountRule, now: datetime) -> list[int]:
        """Клиенты, у которых последний завершённый визит был не позже, чем threshold дней назад,
        нет будущих записей, и купон по этому правилу ещё не выдавался (в пределах лимита)."""
        border = (now - timedelta(days=rule.threshold)).date().isoformat()
        rows = await self._fetchall(
            f"""
            SELECT b.user_id FROM bookings b
            WHERE b.status = 'done'
            GROUP BY b.user_id
            HAVING MAX(b.visit_date) <= ?
               AND NOT EXISTS (SELECT 1 FROM bookings x WHERE x.user_id = b.user_id AND x.status IN {_ACTIVE_SQL})
               AND (SELECT COUNT(*) FROM vouchers v WHERE v.rule_id = ? AND v.user_id = b.user_id AND v.status <> 'revoked') < ?
            """, (border, rule.id, rule.per_client_limit))
        return [r[0] for r in rows]

    # -------------------------------------------------------- schedule blocks
    async def add_block(self, date_from: date, date_to: date, reason: str = "") -> ScheduleBlock:
        async with self._write_lock:
            cur = await self._conn.execute(
                "INSERT INTO schedule_blocks (date_from, date_to, reason, created_at) VALUES (?, ?, ?, ?)",
                (date_from.isoformat(), date_to.isoformat(), reason, utcnow()))
            await self._conn.commit()
            bid = cur.lastrowid
        return ScheduleBlock(bid, date_from.isoformat(), date_to.isoformat(), reason)

    async def list_blocks(self, from_date: date | None = None) -> list[ScheduleBlock]:
        rows = await self._fetchall(
            "SELECT * FROM schedule_blocks WHERE (? IS NULL OR date_to >= ?) ORDER BY date_from",
            (from_date.isoformat() if from_date else None,) * 2)
        return [ScheduleBlock(r["id"], r["date_from"], r["date_to"], r["reason"]) for r in rows]

    async def block_ranges(self, from_date: date | None = None) -> list[tuple[date, date]]:
        return [(date.fromisoformat(b.date_from), date.fromisoformat(b.date_to)) for b in await self.list_blocks(from_date)]

    async def remove_block(self, block_id: int) -> bool:
        async with self._write_lock:
            cur = await self._conn.execute("DELETE FROM schedule_blocks WHERE id = ?", (block_id,))
            await self._conn.commit()
            return cur.rowcount == 1

    # ----------------------------------------------------------------- stats
    async def stats(self) -> Stats:
        by_status = {BookingStatus(r["status"]): int(r["n"])
                     for r in await self._fetchall("SELECT status, COUNT(*) AS n FROM bookings GROUP BY status")}
        users = int((await self._fetchone("SELECT COUNT(*) FROM users"))[0])
        week_iso = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds")
        last_7 = int((await self._fetchone("SELECT COUNT(*) FROM bookings WHERE created_at >= ?", (week_iso,)))[0])
        top = [(r["service_title"], int(r["n"])) for r in await self._fetchall(
            "SELECT service_title, COUNT(*) AS n FROM bookings GROUP BY service_id, service_title"
            " ORDER BY n DESC, service_title ASC LIMIT 3")]
        return Stats(total=sum(by_status.values()), by_status=by_status, users=users, last_7_days=last_7, top_services=top)

    async def analytics(self, since_utc_iso: str) -> dict:
        """Сводка для владельца за период (от since). Все числа считаются в SQL."""
        q = self._fetchone
        created = (await q("SELECT COUNT(*) FROM bookings WHERE created_at >= ?", (since_utc_iso,)))[0]
        by_status = {r[0]: r[1] for r in await self._fetchall(
            "SELECT status, COUNT(*) FROM bookings WHERE created_at >= ? GROUP BY status", (since_utc_iso,))}
        repeat = (await q("SELECT COUNT(*) FROM (SELECT user_id FROM bookings WHERE status = 'done' GROUP BY user_id HAVING COUNT(*) >= 2)"))[0]
        done_clients = (await q("SELECT COUNT(DISTINCT user_id) FROM bookings WHERE status = 'done'"))[0]
        late_cancel = (await q(
            "SELECT COUNT(*) FROM bookings WHERE status = 'cancelled' AND created_at >= ? AND start_at IS NOT NULL"
            " AND replace(substr(updated_at,1,16),'T',' ') >= replace(substr(start_at,1,16),'T',' ')", (since_utc_iso,)))[0]
        reviews_n, reviews_avg = await self.review_summary()
        low = (await q("SELECT COUNT(*) FROM reviews WHERE rating <= 3 AND created_at >= ?", (since_utc_iso,)))[0]
        v_issued = (await q("SELECT COUNT(*) FROM vouchers WHERE issued_at >= ?", (since_utc_iso,)))[0]
        v_used = (await q("SELECT COUNT(*) FROM vouchers WHERE used_at >= ?", (since_utc_iso,)))[0]
        v_saved = (await q("SELECT COALESCE(SUM(discount_rub),0) FROM bookings WHERE status = 'done' AND voucher_id IS NOT NULL AND updated_at >= ?", (since_utc_iso,)))[0]
        jobs = {r[0]: r[1] for r in await self._fetchall(
            "SELECT status, COUNT(*) FROM jobs WHERE created_at >= ? GROUP BY status", (since_utc_iso,))}
        marketing = (await q("SELECT COUNT(*) FROM users WHERE marketing_consent = 1"))[0]
        return {"created": created, "by_status": by_status, "repeat_clients": repeat, "done_clients": done_clients,
                "late_cancel": late_cancel, "reviews": reviews_n, "reviews_avg": reviews_avg, "low_reviews": low,
                "vouchers_issued": v_issued, "vouchers_used": v_used, "discount_total": v_saved,
                "jobs": jobs, "marketing_users": marketing}
