"""Рекомендации по уходу на основе подтверждённой истории и правил студии (content/care.py)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Sequence

from content.care import CARE_RULES, CareRule
from database.models import Booking


@dataclass(frozen=True)
class Recommendation:
    rule: CareRule
    based_on: Booking          # завершённый визит, на основании которого дана рекомендация
    due: date                  # ориентировочная дата
    overdue: bool              # срок наступил


def _visit_day(b: Booking) -> date:
    return date.fromisoformat(b.start_at[:10] if b.start_at else b.visit_date)


def recommendations(done: Sequence[Booking], active: Sequence[Booking], today: date,
                    rules: Sequence[CareRule] = CARE_RULES) -> list[Recommendation]:
    """Рекомендации по ОДНОМУ автомобилю. Правила:
    * основание — только завершённые (подтверждённые студией) визиты;
    * для каждого правила берётся самый свежий подходящий визит;
    * если после него автомобиль уже был на рекомендуемой услуге (или она уже запланирована) — не повторяем;
    * сортировка по ближайшей дате.
    """
    result: list[Recommendation] = []
    for rule in rules:
        basis = [b for b in done if b.service_id in rule.after_services]
        if not basis:
            continue
        latest = max(basis, key=lambda b: (_visit_day(b), b.id))
        latest_day = _visit_day(latest)
        covered = any(b.service_id == rule.recommend_service and _visit_day(b) >= latest_day and b.id != latest.id
                      for b in list(done) + list(active))
        if covered:
            continue
        due = latest_day + timedelta(days=rule.days)
        result.append(Recommendation(rule, latest, due, overdue=due <= today))
    return sorted(result, key=lambda r: r.due)
