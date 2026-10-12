"""Программный расчёт предварительной стоимости (без округлений через float)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from content.catalog import ADDONS_BY_ID, CAR_CLASSES_BY_ID, SERVICES_BY_ID

ROUND_STEP = 100  # итоговая цена класса округляется до сотен рублей


class PricingError(ValueError):
    """Некорректные входные данные расчёта."""


@dataclass(frozen=True)
class EstimateLine:
    title: str
    amount: int


@dataclass(frozen=True)
class Estimate:
    service_id: str
    service_title: str
    car_class_id: str
    car_class_title: str
    coef_pct: int
    base_price: int            # базовая цена «от» для компактного класса
    class_price: int           # цена с учётом класса авто
    addons: tuple[EstimateLine, ...]
    total: int

    @property
    def addon_titles(self) -> tuple[str, ...]:
        return tuple(a.title for a in self.addons)


def format_rub(amount: int) -> str:
    return f"{amount:,}".replace(",", " ") + " ₽"


def format_coef(coef_pct: int) -> str:
    return f"×{coef_pct / 100:.2f}".replace(".", ",")


def _round_to_step(value_x100: int) -> int:
    """value_x100 — цена в копейках×100/100 (целочисленно); округляем half-up до ROUND_STEP."""
    return ((value_x100 + ROUND_STEP // 2) // ROUND_STEP) * ROUND_STEP


def calculate_estimate(service_id: str, car_class_id: str,
                       addon_ids: Iterable[str] = ()) -> Estimate:
    service = SERVICES_BY_ID.get(service_id)
    if service is None:
        raise PricingError(f"Неизвестная услуга: {service_id!r}")
    car_class = CAR_CLASSES_BY_ID.get(car_class_id)
    if car_class is None:
        raise PricingError(f"Неизвестный класс автомобиля: {car_class_id!r}")

    selected: list[str] = []
    for addon_id in addon_ids:
        if addon_id not in ADDONS_BY_ID:
            raise PricingError(f"Неизвестная дополнительная работа: {addon_id!r}")
        if addon_id not in service.addon_ids:
            raise PricingError(f"Работа {addon_id!r} недоступна для услуги {service_id!r}")
        if addon_id not in selected:
            selected.append(addon_id)
    # порядок — как в конфигурации услуги, чтобы расчёт не зависел от порядка кликов
    selected.sort(key=service.addon_ids.index)

    class_price = _round_to_step(service.price_from * car_class.coef_pct // 100)
    lines = tuple(EstimateLine(ADDONS_BY_ID[a].title, ADDONS_BY_ID[a].price) for a in selected)
    total = class_price + sum(line.amount for line in lines)
    return Estimate(
        service_id=service.id, service_title=service.title,
        car_class_id=car_class.id, car_class_title=car_class.title,
        coef_pct=car_class.coef_pct, base_price=service.price_from,
        class_price=class_price, addons=lines, total=total,
    )
