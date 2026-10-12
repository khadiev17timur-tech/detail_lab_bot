"""Модели данных (простые dataclass без ORM)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum


class BookingStatus(str, Enum):
    NEW = "new"
    IN_WORK = "in_work"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DONE = "done"


STATUS_LABELS: dict[BookingStatus, str] = {
    BookingStatus.NEW: "🆕 Новая",
    BookingStatus.IN_WORK: "🛠 В работе",
    BookingStatus.ACCEPTED: "✅ Принята",
    BookingStatus.REJECTED: "❌ Отклонена",
    BookingStatus.DONE: "🏁 Завершена",
}


def parse_status(value: str) -> BookingStatus | None:
    try:
        return BookingStatus(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class NewBooking:
    """Данные для создания заявки."""
    draft_id: str
    user_id: int
    service_id: str
    service_title: str
    car: str
    car_class_id: str
    car_class_title: str
    visit_date: str          # ISO YYYY-MM-DD
    visit_time: str          # HH:MM или «any»
    name: str
    phone: str
    comment: str = ""
    estimate_rub: int | None = None
    addons: tuple[str, ...] = ()
    consent: bool = False


@dataclass(frozen=True)
class Booking:
    id: int
    draft_id: str
    user_id: int
    service_id: str
    service_title: str
    car: str
    car_class_id: str
    car_class_title: str
    visit_date: str
    visit_time: str
    name: str
    phone: str
    comment: str
    estimate_rub: int | None
    addons: tuple[str, ...]
    status: BookingStatus
    consent: bool
    consent_at: str | None
    created_at: str
    updated_at: str
    username: str | None = None
    tg_full_name: str | None = None

    @classmethod
    def from_row(cls, row) -> "Booking":
        try:
            addons = tuple(json.loads(row["addons"] or "[]"))
        except (ValueError, TypeError):
            addons = ()
        return cls(
            id=row["id"], draft_id=row["draft_id"], user_id=row["user_id"],
            service_id=row["service_id"], service_title=row["service_title"],
            car=row["car"], car_class_id=row["car_class_id"], car_class_title=row["car_class_title"],
            visit_date=row["visit_date"], visit_time=row["visit_time"],
            name=row["name"], phone=row["phone"], comment=row["comment"] or "",
            estimate_rub=row["estimate_rub"], addons=addons,
            status=BookingStatus(row["status"]), consent=bool(row["consent"]),
            consent_at=row["consent_at"], created_at=row["created_at"], updated_at=row["updated_at"],
            username=row["username"], tg_full_name=row["tg_full_name"],
        )


@dataclass(frozen=True)
class StatusResult:
    """Итог попытки сменить статус."""
    booking: Booking | None
    changed: bool
    old_status: BookingStatus | None = None


@dataclass(frozen=True)
class Stats:
    total: int
    by_status: dict[BookingStatus, int]
    users: int
    last_7_days: int
    top_services: list[tuple[str, int]] = field(default_factory=list)

    @property
    def new(self) -> int:
        return self.by_status.get(BookingStatus.NEW, 0)

    @property
    def accepted(self) -> int:
        """Принятые: в работе, принятые и завершённые (всё, что не новая и не отклонена)."""
        return sum(self.by_status.get(s, 0)
                   for s in (BookingStatus.IN_WORK, BookingStatus.ACCEPTED, BookingStatus.DONE))

    @property
    def rejected(self) -> int:
        return self.by_status.get(BookingStatus.REJECTED, 0)
