"""Сценарные тесты версии 2: слоты, отмена/перенос, напоминания, отзывы, автомобиль, скидки, админка."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter

from content.studio import STUDIO
from database import BookingStatus, NewBooking
from database.repository import iso_utc
from keyboards.main import BTN_BOOK, BTN_CABINET
from services import availability
from services.scheduler import process_jobs
from tests.conftest import ADMIN_ID, CLIENT_ID

pytestmark = pytest.mark.asyncio

OTHER = 200


def set_studio(env, **changes):
    env.settings = replace(env.settings, studio=replace(env.settings.studio, **changes))
    env.dp.workflow_data["settings"] = env.settings


def tomorrow(env) -> date:
    return availability.local_now(env.settings.studio).date() + timedelta(days=1)


async def pick_until_confirm(h, uid, *, hour="12:00", service="Мойка кузова", car="BMW X5", phone="+79991234567"):
    await h.send(uid, BTN_BOOK)
    await h.click(uid, service)
    await h.send(uid, car)
    await h.click(uid, "Внедорожник")
    await h.click(uid, "Завтра")
    await h.click(uid, hour)
    await h.send(uid, "Иван")
    await h.send(uid, phone)
    await h.send(uid, "-")  # комментарий текстом (минус — любая строка)


async def submit(h, uid):
    await h.click(uid, "Согласен(на) на обработку")
    await h.click(uid, "Отправить заявку")


async def book(h, uid, **kw):
    await pick_until_confirm(h, uid, **kw)
    await submit(h, uid)


def time_buttons(h, uid):
    return [b.text for o in h.visible(uid) for b in h.buttons(o) if len(b.text) == 5 and b.text[2] == ":"]


# ============================================================ свободное время
async def test_time_step_shows_only_free_slots_and_hides_taken_one(env):
    set_studio(env, posts=1)
    await book(env, CLIENT_ID)                                     # клиент A занял 12:00 завтра
    await env.send(OTHER, BTN_BOOK)
    await env.click(OTHER, "Мойка кузова")
    await env.send(OTHER, "Kia Rio")
    await env.click(OTHER, "Компактный")
    await env.click(OTHER, "Завтра")
    shown = time_buttons(env, OTHER)
    assert "12:00" not in shown and "11:00" in shown and "13:00" in shown


async def test_two_posts_let_two_clients_book_the_same_time(env):
    set_studio(env, posts=2)
    await book(env, CLIENT_ID)
    await book(env, OTHER)
    assert await env.repo.count_bookings() == 2


async def test_race_at_submit_second_client_gets_new_choice_and_no_double_booking(env):
    set_studio(env, posts=1)
    await pick_until_confirm(env, CLIENT_ID)
    await pick_until_confirm(env, OTHER)                           # оба дошли до подтверждения с одним слотом
    await submit(env, CLIENT_ID)
    await env.click(OTHER, "Согласен(на) на обработку")
    await env.click(OTHER, "Отправить заявку")
    assert await env.repo.count_bookings() == 1                    # вторая не создана
    assert "только что заняли" in env.last_text(OTHER)
    assert "12:00" not in time_buttons(env, OTHER)                 # и новое время предлагается без занятого


async def test_forged_time_button_for_taken_slot_is_rejected(env):
    set_studio(env, posts=1)
    await book(env, CLIENT_ID)
    await env.send(OTHER, BTN_BOOK)
    await env.click(OTHER, "Мойка кузова")
    await env.send(OTHER, "Kia Rio")
    await env.click(OTHER, "Компактный")
    await env.click(OTHER, "Завтра")
    out, btn = env.find_button(OTHER, "11:00")
    await env.press(OTHER, out, btn.callback_data.replace("1100", "1200"))     # подделка: занятое время
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    draft = await FSMContext(env.dp.storage, StorageKey(env.bot.id, OTHER, OTHER)).get_data()
    assert not draft.get("start_at") and not draft.get("visit_time")             # занятый слот в черновик не попал
    assert await env.repo.count_bookings() == 1


async def test_blocked_dates_are_not_offered(env):
    tmr = tomorrow(env)
    await env.repo.add_block(tmr, tmr, "ремонт")
    await env.send(CLIENT_ID, BTN_BOOK)
    await env.click(CLIENT_ID, "Мойка кузова")
    await env.send(CLIENT_ID, "Kia Rio")
    await env.click(CLIENT_ID, "Компактный")
    labels = [b.text for o in env.visible(CLIENT_ID) for b in env.buttons(o)]
    assert "Завтра" not in labels


async def test_multi_day_service_only_accepts_opening_time_and_blocks_following_day(env):
    set_studio(env, posts=1)
    await env.send(CLIENT_ID, BTN_BOOK)
    await env.click(CLIENT_ID, "Полировка кузова")
    await env.send(CLIENT_ID, "BMW X5")
    await env.click(CLIENT_ID, "Внедорожник")
    await env.click(CLIENT_ID, "Завтра")
    assert time_buttons(env, CLIENT_ID) == ["10:00"]
    await env.click(CLIENT_ID, "10:00")
    await env.send(CLIENT_ID, "Иван")
    await env.send_contact(CLIENT_ID, "79991234567")
    await env.click(CLIENT_ID, "Пропустить")
    await submit(env, CLIENT_ID)
    b = await env.repo.get_booking(1)
    assert b.start_at.endswith("10:00") and b.end_at[:10] == (tomorrow(env) + timedelta(days=1)).isoformat()
    # послезавтра пост ещё занят — мойка на 12:00 в этот день недоступна
    day_after = tomorrow(env) + timedelta(days=1)
    from content.catalog import SERVICES_BY_ID
    free = await availability.slots_for_day(env.repo, env.settings.studio, SERVICES_BY_ID["wash"], day_after,
                                            availability.local_now(env.settings.studio))
    assert free == []                                                           # второй день полировки — пост занят целиком


# ============================================================= мои записи
async def test_my_bookings_lists_and_shows_card(env):
    await book(env, CLIENT_ID)
    await env.send(CLIENT_ID, BTN_CABINET)
    await env.click(CLIENT_ID, "Мои записи")
    assert "Предстоящих: 1" in env.last_text(CLIENT_ID)
    await env.click(CLIENT_ID, "№1")
    card = env.last_text(CLIENT_ID)
    assert "Запись №1" in card and "Мойка кузова" in card and "12:00–13:00" in card
    labels = [b.text for b in env.buttons(env.last(CLIENT_ID))]
    assert "🔁 Перенести" in labels and "❌ Отменить" in labels


async def test_client_cancels_booking_slot_is_freed_and_admin_notified(env):
    set_studio(env, posts=1)
    await book(env, CLIENT_ID)
    await env.send(CLIENT_ID, BTN_CABINET)
    await env.click(CLIENT_ID, "Мои записи")
    await env.click(CLIENT_ID, "№1")
    await env.click(CLIENT_ID, "Отменить")
    assert "Отменить запись №1?" in env.last_text(CLIENT_ID)
    await env.click(CLIENT_ID, "Да, отменить")
    b = await env.repo.get_booking(1)
    assert b.status == BookingStatus.CANCELLED and b.cancelled_by == "client"
    assert "Клиент отменил запись №1" in env.last_text(ADMIN_ID)
    # время освободилось для другого клиента
    from content.catalog import SERVICES_BY_ID
    free = await availability.slots_for_day(env.repo, env.settings.studio, SERVICES_BY_ID["wash"], tomorrow(env),
                                            availability.local_now(env.settings.studio))
    assert any(s.hour == 12 for s in free)


async def test_double_cancel_click_is_safe(env):
    await book(env, CLIENT_ID)
    await env.send(CLIENT_ID, BTN_CABINET)
    await env.click(CLIENT_ID, "Мои записи")
    await env.click(CLIENT_ID, "№1")
    await env.click(CLIENT_ID, "Отменить")
    out, btn = env.find_button(CLIENT_ID, "Да, отменить")
    await asyncio.gather(env.press(CLIENT_ID, out, btn.callback_data), env.press(CLIENT_ID, out, btn.callback_data))
    notices = [o for o in env.visible(ADMIN_ID) if "Клиент отменил" in (o.text or "")]
    assert len(notices) == 1                                        # админ получил одно уведомление
    assert len(await env.repo.status_history(1)) == 2               # new → cancelled, без дублей


async def test_client_cannot_touch_someone_elses_booking(env):
    await book(env, CLIENT_ID)
    out = env.last(CLIENT_ID)
    for action in ("view", "cancel", "cancel_yes", "resched", "ok"):
        await env.press(OTHER, out, f"mine:{action}:1:")
    assert (await env.repo.get_booking(1)).status == BookingStatus.NEW
    assert not [o for o in env.visible(ADMIN_ID) if "отменил" in (o.text or "")]


async def test_client_reschedules_booking(env):
    set_studio(env, posts=1)
    await book(env, CLIENT_ID)
    await env.repo.set_status(1, BookingStatus.ACCEPTED, actor_id=ADMIN_ID)
    await env.send(CLIENT_ID, BTN_CABINET)
    await env.click(CLIENT_ID, "Мои записи")
    await env.click(CLIENT_ID, "№1")
    await env.click(CLIENT_ID, "Перенести")
    await env.click(CLIENT_ID, "Завтра")
    await env.click(CLIENT_ID, "15:00")
    b = await env.repo.get_booking(1)
    assert b.start_at.endswith("15:00") and b.status == BookingStatus.NEW and b.reschedule_count == 1
    note = env.last(ADMIN_ID)
    assert "перенёс запись №1" in note.text
    assert [x.text for x in env.buttons(note)][0] == "✅ Принять в работу"      # админ может подтвердить заново


async def test_reschedule_to_slot_taken_meanwhile_is_refused(env):
    set_studio(env, posts=1)
    await book(env, CLIENT_ID)
    await env.send(CLIENT_ID, BTN_CABINET)
    await env.click(CLIENT_ID, "Мои записи")
    await env.click(CLIENT_ID, "№1")
    await env.click(CLIENT_ID, "Перенести")
    await env.click(CLIENT_ID, "Завтра")
    # пока клиент выбирает, время 15:00 занимает другой
    start = datetime.combine(tomorrow(env), datetime.min.time()).replace(hour=15)
    await env.repo.upsert_user(OTHER, "o", "O")
    await env.repo.create_booking(NewBooking(
        draft_id="x", user_id=OTHER, service_id="wash", service_title="Мойка", car="Kia", car_class_id="compact",
        car_class_title="Компактный", visit_date=start.date().isoformat(), visit_time="15:00", name="О",
        phone="+79990000000", consent=True, start_at=start.strftime("%Y-%m-%dT%H:%M"),
        end_at=(start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")), posts=1)
    out, btn = env.find_button(CLIENT_ID, "15:00") if "15:00" in time_buttons(env, CLIENT_ID) else (None, None)
    if out is not None:
        await env.press(CLIENT_ID, out, btn.callback_data)
    assert (await env.repo.get_booking(1)).start_at.endswith("12:00")        # осталось прежнее время


# ============================================================ напоминания
async def accepted_booking(env, hours_ahead=24, *, uid=CLIENT_ID):
    """Создаёт принятую запись, начало которой через hours_ahead часов; возвращает (booking, now_utc)."""
    studio = env.settings.studio
    now = datetime.now(timezone.utc)
    start = availability.local_now(studio, now) + timedelta(hours=hours_ahead)
    start = start.replace(minute=0, second=0, microsecond=0)
    await env.repo.upsert_user(uid, "u", "U")
    from services.planning import reminder_jobs
    b, _ = await env.repo.create_booking(NewBooking(
        draft_id=f"rem{uid}{hours_ahead}", user_id=uid, service_id="wash", service_title="Мойка кузова", car="BMW X5",
        car_class_id="suv", car_class_title="Внедорожник", visit_date=start.date().isoformat(),
        visit_time=start.strftime("%H:%M"), name="Иван", phone="+79990000001", consent=True,
        start_at=start.strftime("%Y-%m-%dT%H:%M"), end_at=(start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")),
        posts=5, jobs=reminder_jobs(start, now, studio))
    await env.repo.set_status(b.id, BookingStatus.ACCEPTED, actor_id=ADMIN_ID)
    return b, now


async def t24(env, b):
    """Момент сразу после срока напоминания «за 24 часа» для записи b."""
    job = next(j for j in await env.repo.jobs_for_booking(b.id) if j.kind == "remind:24")
    return datetime.fromisoformat(job.due_at) + timedelta(minutes=1)


async def test_reminder_sent_once_with_action_buttons(env):
    b, now = await accepted_booking(env, 30)                         # напоминания за 24 ч и за 2 ч
    due24 = await t24(env, b)
    stats = await process_jobs(env.bot, env.repo, env.settings, due24, pause=0)
    assert stats["sent"] == 1
    msg = env.last(b.user_id)
    assert "Напоминаем о визите завтра" in msg.text
    assert [x.text for x in env.buttons(msg)] == ["✅ Приду", "🔁 Перенести", "❌ Отменить"]
    again = await process_jobs(env.bot, env.repo, env.settings, due24, pause=0)       # повторный проход
    assert again["sent"] == 0 and len([o for o in env.visible(b.user_id) if "Напоминаем" in (o.text or "")]) == 1


async def test_attendance_confirmation_is_idempotent(env):
    b, now = await accepted_booking(env, 30)
    await process_jobs(env.bot, env.repo, env.settings, await t24(env, b), pause=0)
    out, btn = env.find_button(CLIENT_ID, "Приду")
    await env.press(CLIENT_ID, out, btn.callback_data)
    first = (await env.repo.get_booking(b.id)).client_confirmed_at
    assert first is not None
    assert "Приду" not in [x.text for o in env.visible(CLIENT_ID) for x in env.buttons(o) if o.text and "Запись №" in o.text]
    await env.press(CLIENT_ID, out, btn.callback_data)                      # повторное (устаревшее) нажатие
    assert (await env.repo.get_booking(b.id)).client_confirmed_at == first   # время подтверждения не перезаписано


async def test_reminders_survive_restart(env):
    from database import Repository
    b, now = await accepted_booking(env, 30)
    fresh = Repository(env.repo._db)                                  # «новый процесс» над той же базой
    stats = await process_jobs(env.bot, fresh, env.settings, await t24(env, b), pause=0)
    assert stats["sent"] == 1


async def test_no_burst_after_long_downtime(env):
    b, now = await accepted_booking(env, 30)
    # бот «лежал»: проход выполняется через 5 часов после срока напоминания (грейс 30 минут)
    stats = await process_jobs(env.bot, env.repo, env.settings, now + timedelta(hours=11), pause=0)
    assert stats["sent"] == 0 and stats["skipped"] >= 1
    assert not [o for o in env.visible(b.user_id) if "Напоминаем" in (o.text or "")]


async def test_reminder_skipped_for_cancelled_or_unconfirmed_booking(env):
    b, now = await accepted_booking(env, 30)
    await env.repo.set_status(b.id, BookingStatus.CANCELLED, actor_id=b.user_id, cancelled_by="client")
    stats = await process_jobs(env.bot, env.repo, env.settings, await t24(env, b), pause=0)
    assert stats["sent"] == 0
    b2, now2 = await accepted_booking(env, 30, uid=OTHER)
    await env.repo.set_status(b2.id, BookingStatus.NEW, actor_id=ADMIN_ID)        # снова «новая», не подтверждена
    stats = await process_jobs(env.bot, env.repo, env.settings, await t24(env, b2), pause=0)
    assert stats["sent"] == 0


async def test_reminders_follow_configurable_intervals(env):
    set_studio(env, reminder_hours=(48, 6))
    b, _ = await accepted_booking(env, 72)
    kinds = [j.kind for j in await env.repo.jobs_for_booking(b.id)]
    assert kinds == ["remind:48", "remind:6"]


async def test_blocked_client_and_flood_limit_handled(env):
    b, now = await accepted_booking(env, 30)
    env.tg.forbidden_chats.add(b.user_id)
    stats = await process_jobs(env.bot, env.repo, env.settings, await t24(env, b), pause=0)
    assert stats["failed"] == 1 and (await env.repo.jobs_for_booking(b.id))[0].status == "failed"
    env.tg.forbidden_chats.clear()
    b2, now2 = await accepted_booking(env, 30, uid=OTHER)
    env.tg.fail_next[OTHER] = [TelegramRetryAfter(method=None, message="flood", retry_after=7)]
    moment = await t24(env, b2)
    stats = await process_jobs(env.bot, env.repo, env.settings, moment, pause=0)
    assert stats["retry"] == 1
    job = (await env.repo.jobs_for_booking(b2.id))[0]
    assert job.status == "pending" and job.due_at > iso_utc(moment)             # отложено, не потеряно


async def test_network_errors_retry_then_give_up(env):
    b, now = await accepted_booking(env, 30)
    t = await t24(env, b)
    for attempt in range(1, 4):
        env.tg.fail_next[b.user_id] = [TelegramNetworkError(method=None, message="net")]
        stats = await process_jobs(env.bot, env.repo, env.settings, t, pause=0)
        t += timedelta(minutes=6)
        assert stats["retry"] == (1 if attempt < 3 else 0) and stats["failed"] == (0 if attempt < 3 else 1)


# ================================================================== отзывы
async def done_booking(env, *, review_delay_pass=True):
    b, now = await accepted_booking(env, 3)
    res = await env.dp.workflow_data["admin"].change_status(ADMIN_ID, b.id, BookingStatus.DONE)
    assert res.changed
    return b, now


async def test_done_visit_triggers_single_review_request_after_delay(env):
    b, now = await done_booking(env)
    soon = await process_jobs(env.bot, env.repo, env.settings, now + timedelta(minutes=10), pause=0)
    assert soon["sent"] == 0                                                   # рано: задержка 3 часа
    later = await process_jobs(env.bot, env.repo, env.settings, now + timedelta(hours=3, minutes=1), pause=0)
    assert later["sent"] == 1
    msg = env.last(b.user_id)
    assert "Как прошёл визит" in msg.text and [x.text for x in env.buttons(msg)] == [f"{i}⭐" for i in range(1, 6)]
    again = await process_jobs(env.bot, env.repo, env.settings, now + timedelta(hours=9), pause=0)
    assert again["sent"] == 0                                                  # второй запрос не создаётся


async def test_good_rating_gets_public_link_and_cannot_be_repeated(env):
    set_studio(env, review_url="https://reviews.example.com/detail-lab")
    b, now = await done_booking(env)
    await process_jobs(env.bot, env.repo, env.settings, now + timedelta(hours=4), pause=0)
    await env.click(b.user_id, "5⭐")
    review = await env.repo.get_review(b.id)
    assert review.rating == 5
    urls = [x.url for x in env.buttons(env.last(b.user_id)) if x.url]
    assert urls == ["https://reviews.example.com/detail-lab"]
    assert not [o for o in env.visible(ADMIN_ID) if "Низкая оценка" in (o.text or "")]
    out, btn = env.find_button(b.user_id, "Пропустить")
    out2 = next(o for o in env.visible(b.user_id) if "Как прошёл визит" in (o.text or "") or o is out)
    await env.press(b.user_id, out2, f"rev:{b.id}:1")                          # повторная (подделанная) оценка
    assert (await env.repo.get_review(b.id)).rating == 5
    assert (await env.repo.review_summary())[0] == 1


async def test_low_rating_alerts_admin_with_comment(env):
    b, now = await done_booking(env)
    await process_jobs(env.bot, env.repo, env.settings, now + timedelta(hours=4), pause=0)
    await env.click(b.user_id, "2⭐")
    alert = next(o for o in env.visible(ADMIN_ID) if "Низкая оценка" in (o.text or ""))
    assert "2/5" in alert.text and f"№{b.id}" in alert.text
    await env.send(b.user_id, "Долго ждали")
    texts = " ".join(o.text or "" for o in env.visible(ADMIN_ID))
    assert "Долго ждали" in texts and (await env.repo.get_review(b.id)).comment == "Долго ждали"
    await env.send(b.user_id, "Подменю комментарий")                           # второй комментарий не принимается
    assert (await env.repo.get_review(b.id)).comment == "Долго ждали"


async def test_rating_for_unfinished_or_foreign_booking_is_refused(env):
    b, now = await accepted_booking(env, 3)
    await env.repo.upsert_user(OTHER, "o", "O")
    await env.bot.send_message(OTHER, "x")
    sent = env.last(OTHER)
    await env.press(b.user_id, sent, f"rev:{b.id}:5")                           # визит не завершён
    assert await env.repo.get_review(b.id) is None
    await env.repo.set_status(b.id, BookingStatus.DONE, actor_id=ADMIN_ID)
    await env.press(OTHER, sent, f"rev:{b.id}:5")                               # чужая запись
    assert await env.repo.get_review(b.id) is None


# ======================================================= авто и рекомендации
async def test_car_saved_after_booking_and_selectable_next_time(env):
    await book(env, CLIENT_ID)
    await env.send(CLIENT_ID, BTN_BOOK)
    await env.click(CLIENT_ID, "Мойка кузова")
    labels = [b.text for o in env.visible(CLIENT_ID) for b in env.buttons(o)]
    assert "🚗 BMW X5" in labels
    await env.click(CLIENT_ID, "🚗 BMW X5")                                     # класс известен — шаг пропущен
    assert "Выберите <b>дату</b>" in env.last_text(CLIENT_ID)


async def test_my_car_shows_confirmed_history_and_cautious_recommendations(env):
    set_studio(env, review_delay_hours=1)
    b, now = await accepted_booking(env, 3)
    # заменим услугу на полировку: рекомендация «защитить кузов» должна появиться после завершения
    await env.repo._conn.execute("UPDATE bookings SET service_id='polish', service_title='Полировка кузова' WHERE id=?", (b.id,))
    await env.repo._conn.commit()
    await env.send(b.user_id, BTN_CABINET)
    await env.click(b.user_id, "Мой автомобиль")
    await env.click(b.user_id, "BMW X5")
    assert "Пока нет завершённых визитов" in env.last_text(b.user_id) and "Что рекомендует" not in env.last_text(b.user_id)
    await env.repo.set_status(b.id, BookingStatus.DONE, actor_id=ADMIN_ID)
    await env.click(b.user_id, "К автомобилям")
    await env.click(b.user_id, "BMW X5")
    text = env.last_text(b.user_id)
    assert "Полировка кузова" in text and "История обслуживания" in text and "подтверждена студией" in text
    assert "Что рекомендует студия" in text and "ориентир студии, а не гарантия" in text


async def test_add_and_remove_car_manually(env):
    await env.repo.upsert_user(CLIENT_ID, "ivan", "Иван")
    await env.send(CLIENT_ID, BTN_CABINET)
    await env.click(CLIENT_ID, "Мой автомобиль")
    await env.click(CLIENT_ID, "Добавить автомобиль")
    await env.send(CLIENT_ID, "1")
    assert "марку" in env.last_text(CLIENT_ID).lower() or "Укажите" in env.last_text(CLIENT_ID)
    await env.send(CLIENT_ID, "Audi A6")
    await env.click(CLIENT_ID, "Средний")
    assert "Audi A6" in env.last_text(CLIENT_ID)
    await env.click(CLIENT_ID, "Убрать из списка")
    await env.click(CLIENT_ID, "Да, убрать")
    assert await env.repo.list_cars(CLIENT_ID) == []


# ===================================================== согласие и рассылки
async def test_marketing_opt_in_out_and_stop_command(env):
    await env.repo.upsert_user(CLIENT_ID, "ivan", "Иван")
    await env.send(CLIENT_ID, BTN_CABINET)
    await env.click(CLIENT_ID, "Уведомления")
    assert "отключены" in env.last_text(CLIENT_ID)
    await env.click(CLIENT_ID, "Включить предложения")
    assert (await env.repo.get_user(CLIENT_ID)).marketing_consent
    await env.send(CLIENT_ID, "/stop")
    assert not (await env.repo.get_user(CLIENT_ID)).marketing_consent


async def test_marketing_checkbox_at_booking_saves_consent(env):
    await pick_until_confirm(env, CLIENT_ID)
    await env.click(CLIENT_ID, "Присылать персональные предложения")
    await submit(env, CLIENT_ID)
    assert (await env.repo.get_user(CLIENT_ID)).marketing_consent


async def test_care_message_only_with_consent_and_not_twice(env):
    b, now = await accepted_booking(env, 3)
    await env.repo._conn.execute("UPDATE bookings SET service_id='polish', service_title='Полировка' WHERE id=?", (b.id,))
    await env.repo._conn.commit()
    await env.dp.workflow_data["admin"].change_status(ADMIN_ID, b.id, BookingStatus.DONE)
    later = now + timedelta(days=15)
    await process_jobs(env.bot, env.repo, env.settings, later, pause=0)
    care_sent = [o for o in env.visible(b.user_id) if "Защитить" in (o.text or "")]
    assert not care_sent                                                        # нет согласия → не отправляем
    jobs = {j.kind: j.status for j in await env.repo.jobs_for_booking(b.id)}
    assert jobs["care:protect_after_polish"] == "skipped"

    b2, now2 = await accepted_booking(env, 3, uid=OTHER)
    await env.repo._conn.execute("UPDATE bookings SET service_id='polish', service_title='Полировка' WHERE id=?", (b2.id,))
    await env.repo._conn.commit()
    await env.repo.set_marketing(OTHER, True)
    await env.dp.workflow_data["admin"].change_status(ADMIN_ID, b2.id, BookingStatus.DONE)
    await process_jobs(env.bot, env.repo, env.settings, now2 + timedelta(days=15), pause=0)
    msg = [o for o in env.visible(OTHER) if "Защитить" in (o.text or "")]
    assert len(msg) == 1 and "ориентир студии, а не гарантия" in msg[0].text and "/stop" in msg[0].text
    await process_jobs(env.bot, env.repo, env.settings, now2 + timedelta(days=40), pause=0)
    assert len([o for o in env.visible(OTHER) if "Защитить" in (o.text or "")]) == 1


# ===================================================================== скидки
async def test_voucher_applied_at_booking_and_lifecycle(env):
    rule = await env.repo.create_rule(title="−10% постоянному", kind="percent", value=10, trigger="visits",
                                      threshold=1, valid_days=30)
    await env.repo.upsert_user(CLIENT_ID, "ivan", "Иван")
    v = await env.repo.issue_voucher(rule, CLIENT_ID, datetime.now(timezone.utc))
    await pick_until_confirm(env, CLIENT_ID)
    summary = env.last_text(CLIENT_ID)
    assert "с учётом скидки" in summary and "2 070" not in summary.replace(" ", " ") or "скидки" in summary
    await submit(env, CLIENT_ID)
    b = await env.repo.get_booking(1)
    assert b.voucher_id == v.id and b.discount_rub == 230                       # 10% от 2300
    assert (await env.repo.get_voucher(v.id)).status == "reserved"
    await env.repo.set_status(1, BookingStatus.CANCELLED, actor_id=CLIENT_ID, cancelled_by="client")
    assert (await env.repo.get_voucher(v.id)).status == "active"               # вернулась клиенту


async def test_voucher_can_be_declined_at_confirmation(env):
    rule = await env.repo.create_rule(title="−10%", kind="percent", value=10, trigger="visits", threshold=1, valid_days=30)
    await env.repo.upsert_user(CLIENT_ID, "ivan", "Иван")
    await env.repo.issue_voucher(rule, CLIENT_ID, datetime.now(timezone.utc))
    await pick_until_confirm(env, CLIENT_ID)
    await env.click(CLIENT_ID, "Скидка")                                        # выключаем
    await submit(env, CLIENT_ID)
    b = await env.repo.get_booking(1)
    assert b.voucher_id is None and b.discount_rub == 0


async def test_milestone_voucher_after_done_visit_is_sent_only_with_consent(env):
    await env.repo.create_rule(title="−500 ₽ за первый визит", kind="amount", value=500, trigger="visits",
                               threshold=1, valid_days=60)
    b, now = await accepted_booking(env, 3)
    await env.repo.set_marketing(b.user_id, True)
    await env.dp.workflow_data["admin"].change_status(ADMIN_ID, b.id, BookingStatus.DONE)
    from services.lifecycle import after_done
    issued = await after_done(env.bot, env.repo, await env.repo.get_booking(b.id), env.settings.studio)
    assert len(issued) == 1
    assert "персональная скидка 500 ₽" in env.last_text(b.user_id)
    again = await after_done(env.bot, env.repo, await env.repo.get_booking(b.id), env.settings.studio)
    assert again == []                                                          # лимит на клиента: повторно не выдаём


async def test_winback_voucher_issued_once_for_lapsed_clients(env):
    from services.lifecycle import daily_winback
    await env.repo.create_rule(title="Мы скучаем", kind="percent", value=15, trigger="winback", threshold=30, valid_days=14)
    b, now = await accepted_booking(env, 3)
    await env.repo.set_status(b.id, BookingStatus.DONE, actor_id=ADMIN_ID)
    await env.repo.set_marketing(b.user_id, True)
    assert await daily_winback(env.bot, env.repo, env.settings.studio, now + timedelta(days=10)) == []     # рано
    first = await daily_winback(env.bot, env.repo, env.settings.studio, now + timedelta(days=40))
    second = await daily_winback(env.bot, env.repo, env.settings.studio, now + timedelta(days=41))
    assert len(first) == 1 and second == []
    assert "скидка 15%" in env.last_text(b.user_id)


# =============================================================== админ-панель
async def test_admin_schedule_blocks_reviews_analytics_rules(env):
    await book(env, CLIENT_ID)
    await env.send(ADMIN_ID, "/admin")
    await env.click(ADMIN_ID, "Расписание")
    await env.click(ADMIN_ID, "▶️")
    assert "Мойка кузова" in env.last_text(ADMIN_ID) and "12:00–13:00" in env.last_text(ADMIN_ID)
    await env.click(ADMIN_ID, "Панель")
    await env.click(ADMIN_ID, "Закрытые даты")
    await env.click(ADMIN_ID, "Закрыть даты")
    await env.send(ADMIN_ID, "01.01.2001")
    assert "прошла" in env.last_text(ADMIN_ID)
    target = tomorrow(env) + timedelta(days=20)
    await env.send(ADMIN_ID, f"{target:%d.%m}")
    await env.send(ADMIN_ID, "-")
    assert "Даты закрыты" in env.last_text(ADMIN_ID)
    assert await env.repo.block_ranges(date.today()) == [(target, target)]
    await env.click(ADMIN_ID, "Панель")
    await env.click(ADMIN_ID, "Отзывы")
    assert "Пока нет оценок" in env.last_text(ADMIN_ID)
    await env.click(ADMIN_ID, "Панель")
    await env.click(ADMIN_ID, "Аналитика")
    text = env.last_text(ADMIN_ID)
    assert "Заявок создано: <b>1</b>" in text and "Аналитика за 30 дн." in text


async def test_admin_creates_discount_rule_with_wizard(env):
    await env.send(ADMIN_ID, "/admin")
    await env.click(ADMIN_ID, "Скидки")
    await env.click(ADMIN_ID, "Новое правило")
    await env.send(ADMIN_ID, "x")
    assert "от 2 до 60" in env.last_text(ADMIN_ID)
    await env.send(ADMIN_ID, "Скидка постоянному клиенту")
    await env.click(ADMIN_ID, "Процент")
    await env.send(ADMIN_ID, "90")
    assert "от 1 до 50" in env.last_text(ADMIN_ID)
    await env.send(ADMIN_ID, "10")
    await env.click(ADMIN_ID, "После N-го")
    await env.send(ADMIN_ID, "3")
    await env.send(ADMIN_ID, "45")
    assert "Проверьте правило" in env.last_text(ADMIN_ID)
    await env.click(ADMIN_ID, "Сохранить правило")
    rules = await env.repo.list_rules()
    assert [(r.title, r.kind, r.value, r.trigger, r.threshold, r.valid_days, r.active) for r in rules] == [
        ("Скидка постоянному клиенту", "percent", 10, "visits", 3, 45, True)]
    await env.click(ADMIN_ID, "Скидка постоянному клиенту")                       # переключатель
    assert not (await env.repo.list_rules())[0].active


async def test_new_admin_functions_are_closed_to_strangers(env):
    await env.send(CLIENT_ID, "/admin")
    assert "нет доступа" in env.last_text(CLIENT_ID)
    out = env.last(CLIENT_ID)
    for data in ("ax:day:0:", "ax:blocks:0:", "ax:reviews:0:", "ax:analytics:0:", "ax:rules:0:", "ax:rule_new:0:",
                 "ax:block_add:0:", "ax:rule_on:1:", "ax:block_del:1:", "adm:backup:0:::0"):
        await env.press(CLIENT_ID, out, data)
    assert await env.repo.list_rules() == []
    assert not [o for o in env.tg.outbox if o.kind == "SendDocument"]


async def test_admin_service_new_methods_require_admin():
    import tempfile
    from pathlib import Path
    from database import Database, Repository
    from services.admin import AdminService, PermissionDenied
    d = Path(tempfile.mkdtemp())
    db = Database(d / "x.db")
    await db.connect()
    admin = AdminService(Repository(db), {ADMIN_ID}, STUDIO)
    try:
        for coro in (admin.day_bookings(1, date.today()), admin.list_blocks(1), admin.add_block(1, date.today(), date.today()),
                     admin.remove_block(1, 1), admin.recent_reviews(1), admin.analytics(1), admin.list_rules(1),
                     admin.create_rule(1, title="aa", kind="percent", value=5, trigger="visits", threshold=1, valid_days=5),
                     admin.set_rule_active(1, 1, True)):
            with pytest.raises(PermissionDenied):
                await coro
        with pytest.raises(ValueError):
            await admin.create_rule(ADMIN_ID, title="aa", kind="percent", value=99, trigger="visits", threshold=1, valid_days=5)
        with pytest.raises(ValueError):
            await admin.add_block(ADMIN_ID, date(2026, 10, 5), date(2026, 10, 1))
    finally:
        await db.close()


async def test_comment_dash_means_no_comment(env):
    await pick_until_confirm(env, CLIENT_ID)                       # в сценарии комментарий — «-»
    await submit(env, CLIENT_ID)
    assert (await env.repo.get_booking(1)).comment == ""


async def test_opening_admin_panel_resets_unfinished_wizard(env):
    await env.send(ADMIN_ID, "/admin")
    await env.click(ADMIN_ID, "Скидки")
    await env.click(ADMIN_ID, "Новое правило")
    await env.send(ADMIN_ID, "/admin")                              # админ передумал и открыл панель заново
    await env.send(ADMIN_ID, "Случайный текст")
    assert "Не совсем понял" in env.last_text(ADMIN_ID)             # мастер не «перехватил» сообщение
    assert await env.repo.list_rules() == []


async def test_rating_does_not_destroy_booking_draft_in_progress(env):
    b, now = await done_booking(env)
    await process_jobs(env.bot, env.repo, env.settings, now + timedelta(hours=4), pause=0)
    request = next(o for o in env.visible(b.user_id) if "Как прошёл визит" in (o.text or ""))
    await env.send(b.user_id, BTN_BOOK)                              # клиент начал новую запись…
    await env.click(b.user_id, "Мойка кузова")
    await env.press(b.user_id, request, f"rev:{b.id}:4")             # …и нажал старую кнопку оценки
    assert (await env.repo.get_review(b.id)).rating == 4
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    ctx = FSMContext(env.dp.storage, StorageKey(env.bot.id, b.user_id, b.user_id))
    assert await ctx.get_state() == "BookingStates:car"              # черновик записи цел
