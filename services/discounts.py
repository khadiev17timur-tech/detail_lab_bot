"""Расчёт скидок и выдача купонов — только на сервере, по правилам администратора."""
from __future__ import annotations

from datetime import datetime
from typing import Sequence

from database.models import DiscountRule, Voucher
from database.repository import Repository, iso_utc


def discount_amount(kind: str, value: int, price: int, min_price: int = 0) -> int:
    """Сколько рублей скидки даёт купон при цене `price`. 0 — купон не применим. Целые числа, без float."""
    if price <= 0 or price < min_price:
        return 0
    amount = price * value // 100 if kind == "percent" else value
    return max(0, min(int(amount), price))          # скидка не больше цены


def voucher_discount(v: Voucher, price: int) -> int:
    return discount_amount(v.kind, v.value, price, v.min_price)


def best_voucher(vouchers: Sequence[Voucher], price: int, now: datetime) -> tuple[Voucher, int] | None:
    """Самый выгодный применимый купон (просроченные игнорируются). Побеждает большая скидка, затем ранний срок."""
    now_iso = iso_utc(now)
    best: tuple[Voucher, int] | None = None
    for v in vouchers:
        if v.status != "active" or v.expires_at <= now_iso:
            continue
        amount = voucher_discount(v, price)
        if amount <= 0:
            continue
        if best is None or amount > best[1] or (amount == best[1] and v.expires_at < best[0].expires_at):
            best = (v, amount)
    return best


async def issue_milestone_vouchers(repo: Repository, user_id: int, now: datetime) -> list[Voucher]:
    """После завершённого визита: выдаёт купоны по правилам «N-й визит». Лимиты — на стороне базы."""
    visits = await repo.done_visit_count(user_id)
    issued = []
    for rule in await repo.list_rules(active_only=True):
        if rule.trigger == "visits" and rule.threshold == visits:
            v = await repo.issue_voucher(rule, user_id, now)
            if v:
                issued.append(v)
    return issued


async def issue_winback_vouchers(repo: Repository, now: datetime) -> list[Voucher]:
    """Ежедневная проверка «давно не были». Идемпотентна: лимит на клиента не даст выдать повторно."""
    issued = []
    for rule in await repo.list_rules(active_only=True):
        if rule.trigger != "winback":
            continue
        for user_id in await repo.winback_candidates(rule, now):
            v = await repo.issue_voucher(rule, user_id, now)
            if v:
                issued.append(v)
    return issued


def describe_rule(rule: DiscountRule) -> str:
    return rule.describe()
