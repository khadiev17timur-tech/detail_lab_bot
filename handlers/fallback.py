"""Последний роутер: всё, что не поймали остальные."""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from keyboards.main import main_menu
from utils.telegram import ack

router = Router(name="fallback")


@router.message(StateFilter("*"))
async def unknown_message(message: Message, state: FSMContext) -> None:
    if await state.get_state():
        await message.answer("Используйте кнопки под сообщением или отправьте /cancel, чтобы выйти.",
                             reply_markup=main_menu())
    else:
        await message.answer("Не совсем понял 🙈 Выберите раздел в меню ниже или отправьте /help.",
                             reply_markup=main_menu())


@router.callback_query(StateFilter("*"))
async def unknown_callback(cb: CallbackQuery) -> None:
    await ack(cb, "Эта кнопка устарела. Откройте нужный раздел из меню.", alert=True)
