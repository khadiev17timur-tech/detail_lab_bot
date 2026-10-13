"""Админ-панель, часть 2: расписание, закрытые даты, отзывы, аналитика, правила скидок."""
from __future__ import annotations

import logging
from datetime import date

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import Settings
from handlers.admin import IsAdmin
from keyboards import admin as akb
from keyboards.callbacks import AdmX
from services import availability
from services.admin import AdminService
from services.formatting import analytics_text, day_schedule_text, esc, reviews_text
from services.validators import ValidationError, parse_date_range
from states.admin import BlockStates, RuleStates
from utils.telegram import ack, respond

log = logging.getLogger(__name__)

router = Router(name="admin_ext")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


# ------------------------------------------------------------- расписание
@router.callback_query(AdmX.filter(F.a == "day"))
async def cb_day(cb: CallbackQuery, callback_data: AdmX, admin: AdminService, settings: Settings) -> None:
    uid = cb.from_user.id
    try:
        day = date.fromisoformat(callback_data.v) if callback_data.v else availability.local_now(settings.studio).date()
    except ValueError:
        await ack(cb, "Неверная дата.", alert=True)
        return
    bookings = await admin.day_bookings(uid, day)
    blocks = await admin.list_blocks(uid, day)
    blocked = any(date.fromisoformat(b.date_from) <= day <= date.fromisoformat(b.date_to) for b in blocks)
    await respond(cb, day_schedule_text(day, bookings, blocked, settings.studio.posts), akb.day_kb(day))


# ----------------------------------------------------------- закрытые даты
async def _show_blocks(event, admin: AdminService) -> None:
    blocks = await admin.list_blocks(event.from_user.id, availability.local_now_date_default())
    text = "🚫 <b>Закрытые даты</b>\nНа эти дни клиенты не могут записаться."
    if not blocks:
        text += "\n\nСейчас закрытых дат нет."
    await respond(event, text, akb.blocks_kb(blocks))


@router.callback_query(AdmX.filter(F.a == "blocks"))
async def cb_blocks(cb: CallbackQuery, state: FSMContext, admin: AdminService) -> None:
    await state.clear()
    await _show_blocks(cb, admin)


@router.callback_query(AdmX.filter(F.a == "block_add"))
async def cb_block_add(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(BlockStates.dates)
    await respond(cb, "Напишите дату или период, например: <i>25.10</i> или <i>25.10-28.10</i>.", akb.cancel_wizard_kb())


@router.message(BlockStates.dates, F.text, ~F.text.startswith("/"))
async def msg_block_dates(message: Message, state: FSMContext, settings: Settings) -> None:
    try:
        first, last = parse_date_range(message.text, today=availability.local_now(settings.studio).date())
    except ValidationError as exc:
        await message.answer(f"⚠️ {exc}")
        return
    await state.update_data(first=first.isoformat(), last=last.isoformat())
    await state.set_state(BlockStates.reason)
    await message.answer("Причина (для себя) — напишите или отправьте «-», если не нужна.", reply_markup=akb.cancel_wizard_kb())


@router.message(BlockStates.reason, F.text, ~F.text.startswith("/"))
async def msg_block_reason(message: Message, state: FSMContext, admin: AdminService) -> None:
    data = await state.get_data()
    reason = "" if message.text.strip() in {"-", "—"} else message.text.strip()
    try:
        block, conflicts = await admin.add_block(message.from_user.id, date.fromisoformat(data["first"]),
                                                 date.fromisoformat(data["last"]), reason)
    except (ValueError, KeyError) as exc:
        await state.clear()
        await message.answer(f"⚠️ Не удалось закрыть даты: {exc}")
        return
    await state.clear()
    text = f"✅ Даты закрыты: {block.date_from}" + (f" — {block.date_to}" if block.date_to != block.date_from else "")
    if conflicts:
        text += (f"\n\n⚠️ На эти даты уже есть активных записей: {conflicts}. Они не отменены автоматически — "
                 "свяжитесь с клиентами или отклоните заявки вручную.")
    await message.answer(text, reply_markup=akb.back_kb())


@router.callback_query(AdmX.filter(F.a == "block_del"))
async def cb_block_del(cb: CallbackQuery, callback_data: AdmX, admin: AdminService) -> None:
    await admin.remove_block(cb.from_user.id, callback_data.id)
    await ack(cb, "Даты снова открыты")
    await _show_blocks(cb, admin)


# --------------------------------------------------------- отзывы, аналитика
@router.callback_query(AdmX.filter(F.a == "reviews"))
async def cb_reviews(cb: CallbackQuery, admin: AdminService, settings: Settings) -> None:
    reviews, count, avg = await admin.recent_reviews(cb.from_user.id)
    await respond(cb, reviews_text(reviews, count, avg, settings.studio.timezone), akb.back_kb())


@router.callback_query(AdmX.filter(F.a == "analytics"))
async def cb_analytics(cb: CallbackQuery, admin: AdminService) -> None:
    await respond(cb, analytics_text(await admin.analytics(cb.from_user.id, 30), 30), akb.back_kb())


# ------------------------------------------------------------ правила скидок
async def _show_rules(event, admin: AdminService) -> None:
    rules = await admin.list_rules(event.from_user.id)
    lines = ["🎁 <b>Правила скидок</b>", "Нажмите на правило, чтобы включить или выключить.", ""]
    lines += [f"{'🟢' if r.active else '⚪️'} <b>{esc(r.title)}</b> — {esc(r.describe())}" for r in rules] or ["Правил пока нет."]
    lines += ["", "<i>Скидку получает клиент один раз на правило (лимит на клиента), срок действия считается с момента выдачи. "
                  "Сообщение клиенту уходит только при его согласии на предложения.</i>"]
    await respond(event, "\n".join(lines), akb.rules_kb(rules))


@router.callback_query(AdmX.filter(F.a == "rules"))
async def cb_rules(cb: CallbackQuery, state: FSMContext, admin: AdminService) -> None:
    await state.clear()
    await _show_rules(cb, admin)


@router.callback_query(AdmX.filter(F.a.in_({"rule_on", "rule_off"})))
async def cb_rule_toggle(cb: CallbackQuery, callback_data: AdmX, admin: AdminService) -> None:
    await admin.set_rule_active(cb.from_user.id, callback_data.id, callback_data.a == "rule_on")
    await ack(cb, "Готово")
    await _show_rules(cb, admin)


@router.callback_query(AdmX.filter(F.a == "wizard_cancel"))
async def cb_wizard_cancel(cb: CallbackQuery, state: FSMContext, admin: AdminService) -> None:
    await state.clear()
    await ack(cb, "Отменено")
    await _show_rules(cb, admin)


@router.callback_query(AdmX.filter(F.a == "rule_new"))
async def cb_rule_new(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(RuleStates.title)
    await respond(cb, "Название скидки для клиента, например: <i>Скидка постоянному клиенту</i>.", akb.cancel_wizard_kb())


@router.message(RuleStates.title, F.text, ~F.text.startswith("/"))
async def msg_rule_title(message: Message, state: FSMContext) -> None:
    title = message.text.strip()
    if not 2 <= len(title) <= 60:
        await message.answer("⚠️ Название: от 2 до 60 символов.")
        return
    await state.update_data(title=title)
    await state.set_state(RuleStates.kind)
    await message.answer("Тип скидки:", reply_markup=akb.choice_kb([("Процент от стоимости", "percent"), ("Фиксированная сумма, ₽", "amount")]))


@router.callback_query(RuleStates.kind, AdmX.filter(F.a == "pick"))
async def cb_rule_kind(cb: CallbackQuery, callback_data: AdmX, state: FSMContext) -> None:
    if callback_data.v not in ("percent", "amount"):
        await ack(cb, "Выберите вариант кнопкой.", alert=True)
        return
    await state.update_data(kind=callback_data.v)
    await state.set_state(RuleStates.value)
    prompt = "Размер скидки в процентах (1–50):" if callback_data.v == "percent" else "Размер скидки в рублях (50–100000):"
    await respond(cb, prompt, akb.cancel_wizard_kb())


def _positive_int(text: str, low: int, high: int) -> int | None:
    t = text.strip().replace(" ", "")
    return int(t) if t.isdigit() and low <= int(t) <= high else None


@router.message(RuleStates.value, F.text, ~F.text.startswith("/"))
async def msg_rule_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    low, high = (1, 50) if data.get("kind") == "percent" else (50, 100000)
    value = _positive_int(message.text, low, high)
    if value is None:
        await message.answer(f"⚠️ Введите целое число от {low} до {high}.")
        return
    await state.update_data(value=value)
    await state.set_state(RuleStates.trigger)
    await message.answer("Когда выдавать скидку?", reply_markup=akb.choice_kb([
        ("После N-го завершённого визита", "visits"), ("Если клиент давно не был", "winback")]))


@router.callback_query(RuleStates.trigger, AdmX.filter(F.a == "pick"))
async def cb_rule_trigger(cb: CallbackQuery, callback_data: AdmX, state: FSMContext) -> None:
    if callback_data.v not in ("visits", "winback"):
        await ack(cb, "Выберите вариант кнопкой.", alert=True)
        return
    await state.update_data(trigger=callback_data.v)
    await state.set_state(RuleStates.threshold)
    prompt = ("После какого по счёту завершённого визита? Число от 1 до 50:" if callback_data.v == "visits"
              else "Сколько дней без визита считать «давно не был»? Число от 14 до 730:")
    await respond(cb, prompt, akb.cancel_wizard_kb())


@router.message(RuleStates.threshold, F.text, ~F.text.startswith("/"))
async def msg_rule_threshold(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    low, high = (1, 50) if data.get("trigger") == "visits" else (14, 730)
    value = _positive_int(message.text, low, high)
    if value is None:
        await message.answer(f"⚠️ Введите целое число от {low} до {high}.")
        return
    await state.update_data(threshold=value)
    await state.set_state(RuleStates.valid_days)
    await message.answer("Сколько дней действует выданная скидка? Число от 1 до 365:", reply_markup=akb.cancel_wizard_kb())


@router.message(RuleStates.valid_days, F.text, ~F.text.startswith("/"))
async def msg_rule_valid(message: Message, state: FSMContext) -> None:
    value = _positive_int(message.text, 1, 365)
    if value is None:
        await message.answer("⚠️ Введите целое число от 1 до 365.")
        return
    await state.update_data(valid_days=value)
    await state.set_state(RuleStates.confirm)
    d = await state.get_data()
    what = f"{d['value']}%" if d["kind"] == "percent" else f"{d['value']} ₽"
    cond = (f"после {d['threshold']}-го завершённого визита" if d["trigger"] == "visits"
            else f"если клиент не был {d['threshold']}+ дней")
    await message.answer(f"<b>Проверьте правило</b>\n\n«{esc(d['title'])}»\nСкидка: {what}\nКогда: {cond}\n"
                         f"Действует: {value} дн. после выдачи\nЛимит: 1 раз на клиента", reply_markup=akb.confirm_rule_kb())


@router.callback_query(RuleStates.confirm, AdmX.filter(F.a == "rule_save"))
async def cb_rule_save(cb: CallbackQuery, state: FSMContext, admin: AdminService) -> None:
    d = await state.get_data()
    try:
        await admin.create_rule(cb.from_user.id, title=d["title"], kind=d["kind"], value=d["value"],
                                trigger=d["trigger"], threshold=d["threshold"], valid_days=d["valid_days"])
    except (ValueError, KeyError) as exc:
        await state.clear()
        await ack(cb, f"Не удалось сохранить: {exc}", alert=True)
        return
    await state.clear()
    await ack(cb, "Правило сохранено")
    await _show_rules(cb, admin)


@router.message(StateFilter(RuleStates, BlockStates))
async def msg_wizard_unexpected(message: Message) -> None:
    await message.answer("Ответьте текстом или нажмите «Отмена».")
