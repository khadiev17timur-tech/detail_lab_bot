"""Административные операции. Каждая проверяет права вызывающего САМА —
хендлеры и фильтры лишь первый рубеж, а не единственный."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from database.models import Booking, BookingStatus, Stats, StatusResult
from database.repository import Repository
from services.backup import make_backup
from services.lifecycle import done_jobs
from services.export import bookings_to_csv

PAGE_SIZE = 8
LAST_N = 10


class PermissionDenied(Exception):
    """У пользователя нет прав администратора."""


@dataclass(frozen=True)
class Page:
    items: list[Booking]
    total: int
    page: int
    pages: int


class AdminService:
    def __init__(self, repo: Repository, admin_ids: Iterable[int], studio=None):
        self._repo = repo
        self._admin_ids = frozenset(admin_ids)
        self._studio = studio

    @property
    def admin_ids(self) -> frozenset[int]:
        return self._admin_ids

    def is_admin(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self._admin_ids

    def _require(self, actor_id: int | None) -> int:
        if not self.is_admin(actor_id):
            raise PermissionDenied(f"user {actor_id} is not an admin")
        return actor_id  # type: ignore[return-value]

    async def new_count(self, actor_id: int | None) -> int:
        self._require(actor_id)
        return await self._repo.count_bookings(BookingStatus.NEW)

    async def total_count(self, actor_id: int | None) -> int:
        self._require(actor_id)
        return await self._repo.count_bookings()

    async def list_page(self, actor_id: int | None, status: BookingStatus | None,
                        page: int, page_size: int = PAGE_SIZE) -> Page:
        self._require(actor_id)
        total = await self._repo.count_bookings(status)
        pages = max(1, -(-total // page_size))
        page = min(max(0, page), pages - 1)
        items = await self._repo.list_bookings(status, limit=page_size, offset=page * page_size)
        return Page(items, total, page, pages)

    async def last(self, actor_id: int | None, n: int = LAST_N) -> list[Booking]:
        self._require(actor_id)
        return await self._repo.list_bookings(None, limit=n, offset=0)

    async def get(self, actor_id: int | None, booking_id: int) -> Booking | None:
        self._require(actor_id)
        return await self._repo.get_booking(booking_id)

    async def history(self, actor_id: int | None, booking_id: int):
        self._require(actor_id)
        return await self._repo.status_history(booking_id)

    async def change_status(self, actor_id: int | None, booking_id: int, new_status: BookingStatus,
                            expected: BookingStatus | None = None) -> StatusResult:
        actor = self._require(actor_id)
        jobs = []
        if new_status == BookingStatus.DONE and self._studio is not None:
            booking = await self._repo.get_booking(booking_id)
            if booking is not None:
                jobs = done_jobs(booking, datetime.now(timezone.utc), self._studio)
        return await self._repo.set_status(booking_id, new_status, actor_id=actor, expected=expected, jobs=jobs)

    async def stats(self, actor_id: int | None) -> Stats:
        self._require(actor_id)
        return await self._repo.stats()

    async def export_csv(self, actor_id: int | None) -> bytes:
        self._require(actor_id)
        return bookings_to_csv(await self._repo.iter_all_bookings())

    async def backup(self, actor_id: int | None, backup_dir: Path, keep: int = 7) -> Path:
        self._require(actor_id)
        return await make_backup(self._repo, backup_dir, keep=keep)
