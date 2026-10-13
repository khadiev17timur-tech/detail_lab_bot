from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from database.models import STATUS_LABELS, Booking, BookingStatus
from keyboards.callbacks import Adm, AdmX
from services.formatting import list_item_label


def booking_actions_kb(booking: Booking) -> InlineKeyboardMarkup:
    """Кнопки под уведомлением о новой заявке."""
    rows = []
    if booking.status == BookingStatus.NEW:
        rows.append([
            InlineKeyboardButton(text="✅ Принять в работу",
                                 callback_data=Adm(a="set", id=booking.id, s=BookingStatus.IN_WORK.value,
                                                   f=BookingStatus.NEW.value).pack()),
            InlineKeyboardButton(text="❌ Отклонить заявку",
                                 callback_data=Adm(a="set", id=booking.id, s=BookingStatus.REJECTED.value,
                                                   f=BookingStatus.NEW.value).pack()),
        ])
    rows.append([InlineKeyboardButton(text="📋 Подробнее", callback_data=Adm(a="view", id=booking.id).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def booking_view_kb(booking: Booking, back_filter: str = "all", back_page: int = 0) -> InlineKeyboardMarkup:
    """Карточка заявки: смена статуса на любой другой."""
    status_buttons = [
        InlineKeyboardButton(text=STATUS_LABELS[s], callback_data=Adm(a="set", id=booking.id, s=s.value).pack())
        for s in BookingStatus if s != booking.status and s != BookingStatus.CANCELLED
    ]
    rows = [status_buttons[i:i + 2] for i in range(0, len(status_buttons), 2)]
    rows.append([
        InlineKeyboardButton(text="⬅️ К списку", callback_data=Adm(a="list", f=back_filter, p=back_page).pack()),
        InlineKeyboardButton(text="🏠 Панель", callback_data=Adm(a="home").pack()),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def panel_kb(new_count: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🆕 Новые ({new_count})", callback_data=Adm(a="list", f="new").pack())],
        [InlineKeyboardButton(text="🕒 Последние 10", callback_data=Adm(a="last").pack()),
         InlineKeyboardButton(text="📋 Все заявки", callback_data=Adm(a="list", f="all").pack())],
        [InlineKeyboardButton(text="📅 Расписание", callback_data=AdmX(a="day").pack()),
         InlineKeyboardButton(text="🚫 Закрытые даты", callback_data=AdmX(a="blocks").pack())],
        [InlineKeyboardButton(text="⭐ Отзывы", callback_data=AdmX(a="reviews").pack()),
         InlineKeyboardButton(text="📈 Аналитика", callback_data=AdmX(a="analytics").pack())],
        [InlineKeyboardButton(text="🎁 Скидки", callback_data=AdmX(a="rules").pack()),
         InlineKeyboardButton(text="🔎 По статусу", callback_data=Adm(a="filters").pack())],
        [InlineKeyboardButton(text="📊 Статистика", callback_data=Adm(a="stats").pack()),
         InlineKeyboardButton(text="📤 CSV", callback_data=Adm(a="csv").pack()),
         InlineKeyboardButton(text="💾 Бэкап", callback_data=Adm(a="backup").pack())],
        [InlineKeyboardButton(text="✖️ Закрыть", callback_data=Adm(a="close").pack())],
    ])


def filters_kb() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=label, callback_data=Adm(a="list", f=status.value).pack())]
            for status, label in STATUS_LABELS.items()]
    rows.append([InlineKeyboardButton(text="⬅️ Панель", callback_data=Adm(a="home").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def list_kb(items: list[Booking], flt: str, page: int, pages: int, *, show_pager: bool = True) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=list_item_label(b), callback_data=Adm(a="view", id=b.id, f=flt, p=page).pack())]
            for b in items]
    if show_pager and pages > 1:
        pager = []
        if page > 0:
            pager.append(InlineKeyboardButton(text="◀️", callback_data=Adm(a="list", f=flt, p=page - 1).pack()))
        pager.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data=Adm(a="list", f=flt, p=page).pack()))
        if page < pages - 1:
            pager.append(InlineKeyboardButton(text="▶️", callback_data=Adm(a="list", f=flt, p=page + 1).pack()))
        rows.append(pager)
    rows.append([InlineKeyboardButton(text="⬅️ Панель", callback_data=Adm(a="home").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_panel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⬅️ Панель", callback_data=Adm(a="home").pack())]])


# ---------------------------------------------------------------- расширения панели
def _panel_btn() -> InlineKeyboardButton:
    return InlineKeyboardButton(text="🏠 Панель", callback_data=Adm(a="home").pack())


def day_kb(day, has_block_hint: bool = False) -> InlineKeyboardMarkup:
    from datetime import timedelta
    prev_d, next_d = day - timedelta(days=1), day + timedelta(days=1)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️", callback_data=AdmX(a="day", v=prev_d.isoformat()).pack()),
         InlineKeyboardButton(text="Сегодня", callback_data=AdmX(a="day").pack()),
         InlineKeyboardButton(text="▶️", callback_data=AdmX(a="day", v=next_d.isoformat()).pack())],
        [_panel_btn()],
    ])


def blocks_kb(blocks) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"🗑 {b.date_from}" + (f" — {b.date_to}" if b.date_to != b.date_from else ""),
                                  callback_data=AdmX(a="block_del", id=b.id).pack())] for b in blocks]
    rows.append([InlineKeyboardButton(text="➕ Закрыть даты", callback_data=AdmX(a="block_add").pack())])
    rows.append([_panel_btn()])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def rules_kb(rules) -> InlineKeyboardMarkup:
    rows = []
    for r in rules:
        icon = "🟢" if r.active else "⚪️"
        rows.append([InlineKeyboardButton(text=f"{icon} {r.title} ({r.value}{'%' if r.kind == 'percent' else ' ₽'})",
                                          callback_data=AdmX(a="rule_off" if r.active else "rule_on", id=r.id).pack())])
    rows.append([InlineKeyboardButton(text="➕ Новое правило", callback_data=AdmX(a="rule_new").pack())])
    rows.append([_panel_btn()])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cancel_wizard_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✖️ Отмена", callback_data=AdmX(a="wizard_cancel").pack())]])


def choice_kb(options: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=label, callback_data=AdmX(a="pick", v=value).pack())] for label, value in options]
    rows.append([InlineKeyboardButton(text="✖️ Отмена", callback_data=AdmX(a="wizard_cancel").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_rule_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Сохранить правило", callback_data=AdmX(a="rule_save").pack())],
        [InlineKeyboardButton(text="✖️ Отмена", callback_data=AdmX(a="wizard_cancel").pack())]])


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_panel_btn()]])
