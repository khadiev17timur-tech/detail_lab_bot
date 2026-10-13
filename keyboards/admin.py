from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from database.models import STATUS_LABELS, Booking, BookingStatus
from keyboards.callbacks import Adm
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
        [InlineKeyboardButton(text="🔎 По статусу", callback_data=Adm(a="filters").pack()),
         InlineKeyboardButton(text="📊 Статистика", callback_data=Adm(a="stats").pack())],
        [InlineKeyboardButton(text="📤 Выгрузить CSV", callback_data=Adm(a="csv").pack()),
         InlineKeyboardButton(text="💾 Бэкап базы", callback_data=Adm(a="backup").pack())],
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
