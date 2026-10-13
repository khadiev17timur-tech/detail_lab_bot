"""Тексты карточек (HTML для Telegram). Весь пользовательский ввод экранируется."""
from __future__ import annotations

from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from content.catalog import (CAR_CLASSES, CATEGORIES, SERVICES, SERVICES_BY_ID, Service)
from database.models import STATUS_LABELS, Booking, BookingStatus
from services.pricing import format_coef, format_rub
from services.schedule import from_iso
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
def format_when(b: Booking) -> str:
    """Когда визит: «вт, 20.10.2026 · 12:00–13:00» или многодневный интервал."""
    if b.start_at and b.end_at:
        start, end = from_iso(b.start_at), from_iso(b.end_at)
        if start.date() == end.date():
            return f"{format_date_ru(start.date())} · {start:%H:%M}–{end:%H:%M}"
        return f"{format_date_ru(start.date())} {start:%H:%M} → {format_date_ru(end.date())} {end:%H:%M}"
    return f"{format_date_ru(b.visit_date)} · {format_time_ru(b.visit_time)}"


def _price_line(b: Booking) -> str | None:
    if b.estimate_rub is None:
        return None
    if b.discount_rub:
        return (f"💰 Предварительно: от {format_rub(max(0, b.estimate_rub - b.discount_rub))} "
                f"<i>(с учётом скидки {format_rub(b.discount_rub)}; оценка, не окончательная цена)</i>")
    return f"💰 Предварительно: от {format_rub(b.estimate_rub)} <i>(оценка, не окончательная цена)</i>"


def _booking_lines(b: Booking) -> list[str]:
    lines = [
        f"🚘 Услуга: <b>{esc(b.service_title)}</b>",
        f"🚗 Автомобиль: {esc(b.car)} ({esc(b.car_class_title.lower())})",
        f"📅 Время: {esc(format_when(b))}",
        f"👤 Имя: {esc(b.name)}",
        f"📞 Телефон: {esc(format_phone(b.phone))}",
    ]
    if b.addons:
        lines.append("➕ Доп. работы: " + esc(", ".join(b.addons)))
    if b.comment:
        lines.append(f"💬 Комментарий: {esc(b.comment)}")
    price = _price_line(b)
    if price:
        lines.append(price)
    return lines


def client_booking_card(b: Booking) -> str:
    reserved = ("Время предварительно зарезервировано за вами. " if b.start_at else "")
    return "\n".join([
        f"✅ <b>Заявка №{b.id} отправлена</b>",
        "",
        *_booking_lines(b),
        "",
        f"ℹ️ {reserved}Менеджер подтвердит запись и согласует окончательную стоимость. "
        "Изменить или отменить запись можно в разделе «Мой кабинет → Мои записи».",
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
        held = f"Время {esc(format_when(b))} закреплено за вами. " if b.start_at else ""
        return (f"🛠 <b>Заявка №{n} принята в работу</b>\n\n{held}"
                "Менеджер свяжется с вами, чтобы уточнить детали и окончательную стоимость. "
                "Запись считается подтверждённой только после этого согласования.")
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


# ------------------------------------------------------------- кабинет клиента
def my_booking_label(b: Booking) -> str:
    when = format_when(b)
    short = when.split(" · ")[0].split(", ")[-1] if " · " in when else when[:16]
    label = f"{STATUS_LABELS[b.status].split(' ', 1)[0]} №{b.id} · {short} · {b.service_title}"
    return label if len(label) <= 58 else label[:57] + "…"


def my_booking_card(b: Booking, studio) -> str:
    lines = [f"📅 <b>Запись №{b.id}</b>", f"Статус: <b>{STATUS_LABELS[b.status]}</b>", "", *_booking_lines(b)]
    if b.client_confirmed_at:
        lines.append("✔️ Вы подтвердили, что придёте")
    if b.status == BookingStatus.NEW:
        lines += ["", "ℹ️ Время за вами зарезервировано, менеджер скоро подтвердит запись."]
    return "\n".join(lines)


def reminder_text(b: Booking, hours: int) -> str:
    if hours >= 24 and hours % 24 == 0:
        left = "завтра" if hours == 24 else f"через {hours // 24} дн."
    else:
        left = f"через {hours} ч."
    return (f"⏰ <b>Напоминаем о визите {left}</b>\n\n"
            f"🚘 {esc(b.service_title)}\n🚗 {esc(b.car)}\n📅 {esc(format_when(b))}\n\n"
            "Если планы изменились — перенесите или отмените запись заранее: это освободит время для других клиентов.")


def review_request_text(b: Booking) -> str:
    return (f"🙏 <b>Как прошёл визит?</b>\n\n{esc(b.service_title)}, {esc(b.car)}.\n"
            "Оцените, пожалуйста, качество работы — это займёт несколько секунд.")


def review_thanks_text(rating: int, review_url: str, low: bool) -> str:
    if low:
        return ("Спасибо за честную оценку. Нам жаль, что что-то пошло не так — менеджер изучит ситуацию "
                "и свяжется с вами. Если хотите, напишите, что именно не понравилось.")
    base = "Спасибо за оценку! Нам важно ваше мнение."
    if review_url:
        base += "\nЕсли хотите поддержать нас, оставьте отзыв на публичной странице — это очень помогает."
    return base


def low_rating_alert(b: Booking, rating: int, comment: str, tz_name: str) -> str:
    lines = [f"⚠️ <b>Низкая оценка: {'⭐' * rating} ({rating}/5)</b>",
             f"Запись №{b.id} · {esc(b.service_title)}",
             f"Клиент: {esc(b.name)} · {esc(format_phone(b.phone))}",
             f'Telegram: <a href="tg://user?id={b.user_id}">{b.user_id}</a>']
    if comment:
        lines.append(f"Комментарий: {esc(comment)}")
    return "\n".join(lines)


def stars(n: int) -> str:
    return "⭐" * n + "☆" * (5 - n)


# --------------------------------------------------------------------- админка v2
def day_schedule_text(day, bookings: list[Booking], blocked: bool, posts: int) -> str:
    lines = [f"📅 <b>Расписание · {esc(format_date_ru(day))}</b>", f"Постов: {posts}"]
    if blocked:
        lines.append("🚫 День закрыт для записи")
    if not bookings:
        lines += ["", "Записей нет."]
        return "\n".join(lines)
    lines.append("")
    for b in bookings:
        when = f"{from_iso(b.start_at):%H:%M}–{from_iso(b.end_at):%H:%M}" if b.start_at and b.end_at else format_time_ru(b.visit_time)
        mark = {"new": "🆕", "in_work": "🛠", "accepted": "✅"}.get(b.status.value, "•")
        lines.append(f"{mark} {when} · №{b.id} · {esc(b.service_title)} · {esc(b.car)} · {esc(b.name)}")
    return "\n".join(lines)


def reviews_text(reviews, count: int, avg, tz_name: str) -> str:
    head = "⭐ <b>Отзывы</b>"
    if not count:
        return f"{head}\n\nПока нет оценок."
    lines = [head, f"Всего: {count} · средняя оценка: <b>{avg:.2f}</b>", ""]
    for r in reviews:
        lines.append(f"{stars(r.rating)} · запись №{r.booking_id} · {local_dt(r.created_at, tz_name)}")
        if r.comment:
            lines.append(f"   «{esc(r.comment[:200])}»")
    return "\n".join(lines)


def analytics_text(a: dict, days: int) -> str:
    created = a["created"]
    by = a["by_status"]
    cancelled = by.get("cancelled", 0)
    rate = f"{cancelled * 100 // created}%" if created else "—"
    jobs = a["jobs"]
    lines = [
        f"📈 <b>Аналитика за {days} дн.</b>",
        "",
        f"Заявок создано: <b>{created}</b>",
        f"• завершено: {by.get('done', 0)} · в работе/приняты: {by.get('in_work', 0) + by.get('accepted', 0)} · новые: {by.get('new', 0)}",
        f"• отклонено студией: {by.get('rejected', 0)} · отменено клиентами: {cancelled} ({rate})",
        f"• из отмен «поздних» (после начала визита): {a['late_cancel']}",
        "",
        f"Клиентов с завершённым визитом: {a['done_clients']}, из них вернулись (2+ визита): <b>{a['repeat_clients']}</b>",
        "",
        f"Отзывов: {a['reviews']}" + (f" · средняя {a['reviews_avg']:.2f}" if a["reviews_avg"] else "") + f" · низких за период: {a['low_reviews']}",
        "",
        f"Скидки: выдано {a['vouchers_issued']}, использовано {a['vouchers_used']}, предоставлено на {format_rub(a['discount_total'])}",
        f"Согласились на предложения: {a['marketing_users']} клиентов",
        "",
        f"Сообщения по расписанию: отправлено {jobs.get('sent', 0)}, пропущено {jobs.get('skipped', 0)}, "
        f"не доставлено {jobs.get('failed', 0)}, ждут {jobs.get('pending', 0)}",
    ]
    return "\n".join(lines)
