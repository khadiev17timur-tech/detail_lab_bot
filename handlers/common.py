"""/start, /menu, /help, /cancel и возврат в главное меню."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import Settings
from keyboards.callbacks import Menu
from keyboards.main import main_menu
from utils.flow import reset_flow
from utils.telegram import ack, send

router = Router(name="common")


def welcome_text(settings: Settings) -> str:
    s = settings.studio
    text = (
        f"Добро пожаловать в {s.name} 🖤\n"
        f"{s.tagline}\n\n"
        "Здесь можно узнать стоимость, выбрать свободное время и записаться на детейлинг за пару минут. "
        "Напомним о визите, а в личном кабинете сохраним историю обслуживания вашего автомобиля."
    )
    if s.demo:
        text += "\n\n" + s.demo_notice
    return text


HELP_TEXT = (
    "<b>Как пользоваться ботом</b>\n\n"
    "Все разделы доступны кнопками внизу. Также работают команды:\n"
    "/start — главное меню\n"
    "/services — каталог услуг\n"
    "/prices — прайс-лист\n"
    "/calc — калькулятор стоимости\n"
    "/book — записаться (покажем только свободное время)\n"
    "/cabinet — мои записи, перенос и отмена\n"
    "/car — мой автомобиль и история обслуживания\n"
    "/stop — отключить персональные предложения\n"
    "/portfolio — примеры работ\n"
    "/faq — частые вопросы\n"
    "/contacts — контакты\n"
    "/manager — связаться с менеджером\n"
    "/cancel — отменить текущее действие"
)


@router.message(CommandStart(), StateFilter("*"))
async def cmd_start(message: Message, state: FSMContext, settings: Settings) -> None:
    await state.clear()
    await message.answer(welcome_text(settings), reply_markup=main_menu())


@router.message(Command("menu"), StateFilter("*"))
async def cmd_menu(message: Message, state: FSMContext) -> None:
    interrupted = await reset_flow(message, state)
    if not interrupted:
        await message.answer("🏠 Главное меню", reply_markup=main_menu())


@router.message(Command("help"), StateFilter("*"))
async def cmd_help(message: Message, state: FSMContext) -> None:
    await message.answer(HELP_TEXT, reply_markup=main_menu())


@router.message(Command("cancel"), StateFilter("*"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current is None:
        await message.answer("Сейчас нечего отменять. Выберите раздел в меню 👇", reply_markup=main_menu())
        return
    await state.clear()
    await message.answer("Действие отменено. Данные не сохранены.", reply_markup=main_menu())


@router.callback_query(Menu.filter(F.action == "home"), StateFilter("*"))
async def cb_home(cb: CallbackQuery, state: FSMContext) -> None:
    await ack(cb)
    interrupted = await reset_flow(cb, state)
    if not interrupted:
        await send(cb, "🏠 Главное меню", main_menu())
