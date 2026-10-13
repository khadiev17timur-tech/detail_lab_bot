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
    CANCELLED = "cancelled"


# Статусы, при которых время в календаре занято (слот держится за клиентом)
ACTIVE_STATUSES = (BookingStatus.NEW, BookingStatus.IN_WORK, BookingStatus.ACCEPTED)


STATUS_LABELS: dict[BookingStatus, str] = {
    BookingStatus.NEW: "🆕 Новая",
    BookingStatus.IN_WORK: "🛠 В работе",
    BookingStatus.ACCEPTED: "✅ Принята",
    BookingStatus.REJECTED: "❌ Отклонена",
    BookingStatus.DONE: "🏁 Завершена",
    BookingStatus.CANCELLED: "🚫 Отменена клиентом",
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
    start_at: str | None = None        # YYYY-MM-DDTHH:MM по времени студии
    end_at: str | None = None
    car_id: int | None = None
    voucher_id: int | None = None
    discount_rub: int = 0


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
    start_at: str | None = None
    end_at: str | None = None
    car_id: int | None = None
    voucher_id: int | None = None
    discount_rub: int = 0
    client_confirmed_at: str | None = None
    cancelled_by: str | None = None
    reschedule_count: int = 0

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

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
            start_at=row["start_at"], end_at=row["end_at"], car_id=row["car_id"],
            voucher_id=row["voucher_id"], discount_rub=row["discount_rub"] or 0,
            client_confirmed_at=row["client_confirmed_at"], cancelled_by=row["cancelled_by"],
            reschedule_count=row["reschedule_count"] or 0,
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


@dataclass(frozen=True)
class Car:
    id: int
    user_id: int
    title: str
    car_class_id: str | None
    created_at: str


@dataclass(frozen=True)
class Job:
    id: int
    kind: str
    booking_id: int
    user_id: int
    due_at: str
    status: str
    attempts: int


@dataclass(frozen=True)
class Review:
    id: int
    booking_id: int
    user_id: int
    rating: int
    comment: str
    created_at: str


@dataclass(frozen=True)
class DiscountRule:
    id: int
    title: str
    kind: str            # percent | amount
    value: int
    trigger: str         # visits | winback
    threshold: int
    valid_days: int
    per_client_limit: int
    min_price: int
    active: bool

    def describe(self) -> str:
        what = f"{self.value}%" if self.kind == "percent" else f"{self.value} ₽"
        cond = (f"после {self.threshold}-го визита" if self.trigger == "visits"
                else f"не были {self.threshold}+ дн.")
        return f"{what} · {cond} · действует {self.valid_days} дн."


@dataclass(frozen=True)
class Voucher:
    id: int
    rule_id: int
    user_id: int
    title: str
    kind: str
    value: int
    min_price: int
    issued_at: str
    expires_at: str
    status: str          # active | reserved | used | revoked
    booking_id: int | None


@dataclass(frozen=True)
class ScheduleBlock:
    id: int
    date_from: str
    date_to: str
    reason: str


@dataclass(frozen=True)
class UserRow:
    tg_id: int
    username: str | None
    full_name: str | None
    marketing_consent: bool
    marketing_asked: bool
