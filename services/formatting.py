"""Тексты карточек (HTML для Telegram). Весь пользовательский ввод экранируется."""
from __future__ import annotations

from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from content.catalog import (CAR_CLASSES, CATEGORIES, SERVICES, SERVICES_BY_ID, Service)
from database.models import STATUS_LABELS, Booking, BookingStatus
from services.pricing import format_coef, format_rub
from services.validators import format_date_ru, format_phone, format_time_ru

PRICE_DISCLAIMER = (
    "⚠️ <i>Цены ориентировочные. Итоговая стоимость зависит от модели и состояния "
    "автомобиля, объёма работ и выбранных материалов. Точная цена подтверждается "
    "после консультации.</i>"
)


def esc(value: object) -> str:
    return escape(str(value), quote=False)


def local_dt(iso_utc: str, tz_name: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_utc).astimezone(ZoneInfo(tz_name))
        return dt.strftime("%d.%m.%Y %H:%M")
    except (ValueError, TypeError):
        return iso_utc


# ------------------------------------------------------------------- услуги
def service_card(service: Service, demo_notice: str = "") -> str:
    benefits = "\n".join(f"• {esc(b)}" for b in service.benefits)
    parts = [
        f"{service.emoji} <b>{esc(service.title)}</b>",
        "",
        esc(service.description),
        "",
        "<b>Преимущества</b>",
        benefits,
        "",
        f"💰 <b>от {format_rub(service.price_from)}</b> <i>(компактный класс; для других классов — "
        "через калькулятор)</i>",
        f"⏱ Ориентировочно: {esc(service.duration)}",
        "",
        PRICE_DISCLAIMER,
    ]
    if demo_notice:
        parts += ["", demo_notice]
    return "\n".join(parts)


def catalog_text(demo_notice: str = "") -> str:
    lines = ["🚘 <b>Каталог услуг</b>", "Выберите услугу, чтобы узнать подробности:", ""]
    for cat in CATEGORIES:
        items = [s for s in SERVICES if s.category_id == cat.id]
        if not items:
            continue
        lines.append(f"<b>{esc(cat.title)}</b>")
        lines += [f"{s.emoji} {esc(s.short_title)} — от {format_rub(s.price_from)}" for s in items]
        lines.append("")
    if demo_notice:
        lines.append(demo_notice)
    return "\n".join(lines).rstrip()


def price_list_text(demo_notice: str = "") -> str:
    lines = ["💰 <b>Прайс-лист</b>", "<i>Цены «от» указаны для компактного класса автомобиля.</i>", ""]
    for cat in CATEGORIES:
        items = [s for s in SERVICES if s.category_id == cat.id]
        if not items:
            continue
        lines.append(f"<b>{esc(cat.title)}</b>")
        for s in items:
            lines.append(f"• {esc(s.title)} — <b>от {format_rub(s.price_from)}</b> · {esc(s.duration)}")
        lines.append("")
    lines.append("<b>Поправка на класс автомобиля</b>")
    lines += [f"• {esc(c.title)} ({esc(c.examples)}) — {format_coef(c.coef_pct)}" for c in CAR_CLASSES]
    lines += ["", PRICE_DISCLAIMER]
    if demo_notice:
        lines += ["", demo_notice]
    return "\n".join(lines)


# ------------------------------------------------------------------- заявки
def _booking_lines(b: Booking) -> list[str]:
    lines = [
        f"🚘 Услуга: <b>{esc(b.service_title)}</b>",
        f"🚗 Автомобиль: {esc(b.car)} ({esc(b.car_class_title.lower())})",
        f"📅 Желаемая дата: {esc(format_date_ru(b.visit_date))}",
        f"🕘 Желаемое время: {esc(format_time_ru(b.visit_time))}",
        f"👤 Имя: {esc(b.name)}",
        f"📞 Телефон: {esc(format_phone(b.phone))}",
    ]
    if b.addons:
        lines.append("➕ Доп. работы: " + esc(", ".join(b.addons)))
    if b.comment:
        lines.append(f"💬 Комментарий: {esc(b.comment)}")
    if b.estimate_rub is not None:
        lines.append(f"💰 Предварительно: от {format_rub(b.estimate_rub)} <i>(оценка, не окончательная цена)</i>")
    return lines


def client_booking_card(b: Booking) -> str:
    return "\n".join([
        f"✅ <b>Заявка №{b.id} отправлена</b>",
        "",
        *_booking_lines(b),
        "",
        "ℹ️ Это заявка, а не подтверждённая запись. Менеджер свяжется с вами, чтобы "
        "согласовать дату, время и окончательную стоимость.",
    ])


def admin_booking_card(b: Booking, *, tz_name: str, header: str | None = None,
                       history: list[tuple[str | None, str, int | None, str]] | None = None) -> str:
    user_link = f'<a href="tg://user?id={b.user_id}">{b.user_id}</a>'
    username = f" · @{esc(b.username)}" if b.username else ""
    lines = [
        header or f"📋 <b>Заявка №{b.id}</b>",
        f"Статус: <b>{STATUS_LABELS[b.status]}</b>",
        f"Создана: {local_dt(b.created_at, tz_name)} · обновлена: {local_dt(b.updated_at, tz_name)}",
        "",
        *_booking_lines(b),
        "",
        f"🆔 Telegram: {user_link}{username}",
    ]
    if history:
        lines += ["", "<b>История статусов</b>"]
        for old, new, actor, at in history[-6:]:
            who = f"админ {actor}" if actor else "система"
            lines.append(f"• {local_dt(at, tz_name)} — {STATUS_LABELS[BookingStatus(new)]} ({who})")
    return "\n".join(lines)


def new_booking_admin_text(b: Booking, *, tz_name: str) -> str:
    return admin_booking_card(b, tz_name=tz_name, header=f"🔔 <b>Новая заявка №{b.id}</b>")


def list_item_label(b: Booking) -> str:
    icon = STATUS_LABELS[b.status].split(" ", 1)[0]
    title = SERVICES_BY_ID[b.service_id].short_title if b.service_id in SERVICES_BY_ID else b.service_title
    label = f"{icon} №{b.id} · {b.name} · {title}"
    return label if len(label) <= 58 else label[:57] + "…"


# --------------------------------------------------- сообщения клиенту о статусе
def client_status_text(b: Booking, studio_name: str) -> str | None:
    """Текст клиенту при смене статуса. None — клиента не уведомляем."""
    n = b.id
    if b.status == BookingStatus.IN_WORK:
        return (f"🛠 <b>Заявка №{n} принята в работу</b>\n\n"
                "Менеджер скоро свяжется с вами, чтобы согласовать дату, время и "
                "окончательную стоимость. Запись считается подтверждённой только после этого согласования.")
    if b.status == BookingStatus.ACCEPTED:
        return (f"✅ <b>Заявка №{n} принята</b>\n\n"
                "Менеджер уточнит с вами детали. Запись будет окончательно подтверждена "
                "после согласования даты, времени и стоимости.")
    if b.status == BookingStatus.REJECTED:
        return (f"❌ <b>По заявке №{n} не получится взять работу</b>\n\n"
                "К сожалению, мы не можем принять её в таком виде. Вы можете оформить новую "
                "заявку на другую дату или уточнить детали у менеджера.")
    if b.status == BookingStatus.DONE:
        return (f"🏁 <b>Заявка №{n} завершена</b>\n\n"
                f"Спасибо, что выбрали {esc(studio_name)} 🖤 Будем рады видеть вас снова!")
    return None


# ------------------------------------------------------------------ статистика
def stats_text(stats) -> str:
    lines = [
        "📊 <b>Статистика заявок</b>",
        "",
        f"Всего заявок: <b>{stats.total}</b>",
        f"🆕 Новых: <b>{stats.new}</b>",
        f"✅ Принятых (в работе, принятые, завершённые): <b>{stats.accepted}</b>",
        f"❌ Отклонённых: <b>{stats.rejected}</b>",
        "",
        "<b>По статусам</b>",
    ]
    for status, label in STATUS_LABELS.items():
        lines.append(f"• {label}: {stats.by_status.get(status, 0)}")
    lines += ["", f"За последние 7 дней: {stats.last_7_days}", f"Клиентов в базе: {stats.users}"]
    if stats.top_services:
        lines += ["", "<b>Популярные услуги</b>"]
        lines += [f"• {esc(t)} — {n}" for t, n in stats.top_services]
    return "\n".join(lines)
