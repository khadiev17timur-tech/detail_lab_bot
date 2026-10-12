"""Сброс активных сценариев (FSM)."""
from __future__ import annotations

from aiogram.fsm.context import FSMContext

from keyboards.main import main_menu
from states.booking import BookingStates
from utils.telegram import Event, send


async def reset_flow(event: Event, state: FSMContext, *, notify: bool = True) -> bool:
    """Очищает FSM. Если прервана запись — предупреждает и возвращает главное меню.

    Возвращает True, если был прерван сценарий записи.
    """
    current = await state.get_state()
    await state.clear()
    interrupted = bool(current) and current.startswith(BookingStates.__name__)
    if interrupted and notify:
        await send(event, "↩️ Оформление заявки прервано, данные не сохранены.", main_menu())
    return interrupted
