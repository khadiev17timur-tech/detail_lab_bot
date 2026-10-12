"""Сведения о студии. Всё, что нужно поменять под конкретного заказчика, — здесь.

Часть полей можно переопределить через .env (см. .env.example), чтобы не править код.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Studio:
    name: str = "DETAIL LAB"
    tagline: str = "Уход за автомобилем, который заметен с первого взгляда."
    about: str = (
        "Премиальная детейлинг-студия: бережная мойка, полировка, защитные "
        "покрытия и уход за салоном."
    )

    # --- контакты ------------------------------------------------------------
    address: str = "г. Демо-город, ул. Примерная, 1 (демо-адрес)"
    work_hours: str = "Ежедневно, 10:00–20:00"
    phone: str = "+7 (000) 000-00-00 (демо-номер)"
    manager_username: str = ""   # без @; пусто = не настроен
    map_url: str = ""            # https://...; пусто = ссылка не показывается
    website_url: str = ""        # https://...; пусто = ссылка не показывается

    # --- режим работы для проверки желаемого времени --------------------------
    open_time: str = "10:00"
    close_time: str = "20:00"
    visit_times: tuple[str, ...] = (
        "10:00", "11:00", "12:00", "13:00", "14:00",
        "15:00", "16:00", "17:00", "18:00", "19:00",
    )
    booking_horizon_days: int = 90   # на сколько дней вперёд принимаем желаемую дату
    timezone: str = "Europe/Moscow"

    # --- демо-режим ----------------------------------------------------------
    demo: bool = True
    demo_notice: str = (
        "ℹ️ <i>Демо-версия: студия, цены, адреса и контакты вымышленные "
        "и показаны для примера.</i>"
    )

    @property
    def manager_url(self) -> str:
        return f"https://t.me/{self.manager_username}" if self.manager_username else ""


STUDIO = Studio()
