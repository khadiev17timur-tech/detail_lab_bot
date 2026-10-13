"""Сценарные тесты: настоящий Dispatcher, поддельный Telegram."""
from __future__ import annotations

from dataclasses import replace

import pytest

from content.catalog import SERVICES
from database.models import BookingStatus
from keyboards.main import (BTN_BOOK, BTN_CALC, BTN_CONTACTS, BTN_FAQ, BTN_MANAGER,
                            BTN_PORTFOLIO, BTN_PRICES, BTN_SERVICES)
from tests.conftest import ADMIN_ID, CLIENT_ID

pytestmark = pytest.mark.asyncio


async def fill_booking(h, uid=CLIENT_ID, *, service_button="Мойка кузова", phone="+7 999 123-45-67"):
    """Проходит всю запись до экрана подтверждения."""
    await h.send(uid, BTN_BOOK)
    await h.click(uid, service_button)
    await h.send(uid, "BMW X5")
    await h.click(uid, "Внедорожник")
    await h.click(uid, "Завтра")
    await h.click(uid, "12:00")
    await h.send(uid, "Иван")
    await h.send(uid, phone)
    await h.send(uid, "Позвоните после 18:00")


# ------------------------------------------------------------ главное меню
async def test_start_shows_welcome_and_menu(env):
    await env.send(CLIENT_ID, "/start")
    out = env.last(CLIENT_ID)
    assert "Добро пожаловать в DETAIL LAB" in out.text
    assert "Демо-версия" in out.text
    labels = {b.text for row in out.markup.keyboard for b in row}
    assert {BTN_SERVICES, BTN_PRICES, BTN_BOOK, BTN_CALC, BTN_PORTFOLIO, BTN_FAQ, BTN_CONTACTS,
            BTN_MANAGER} <= labels


@pytest.mark.parametrize("button,expected", [
    (BTN_SERVICES, "Каталог услуг"),
    (BTN_PRICES, "Прайс-лист"),
    (BTN_CALC, "Калькулятор"),
    (BTN_PORTFOLIO, "Портфолио"),
    (BTN_FAQ, "Частые вопросы"),
    (BTN_CONTACTS, "контакты"),
    (BTN_MANAGER, "менеджером"),
    ("/help", "Как пользоваться"),
])
async def test_every_menu_button_answers(env, button, expected):
    await env.send(CLIENT_ID, "/start")
    await env.send(CLIENT_ID, button)
    assert expected in env.last_text(CLIENT_ID)


async def test_every_section_has_way_home(env):
    await env.send(CLIENT_ID, BTN_SERVICES)
    await env.click(CLIENT_ID, "В меню")
    assert "Главное меню" in env.last_text(CLIENT_ID)


# ---------------------------------------------------------- услуги и прайс
async def test_all_ten_services_open_with_book_and_back_buttons(env):
    assert len(SERVICES) == 10
    for svc in SERVICES:
        await env.send(CLIENT_ID, BTN_SERVICES)
        await env.click(CLIENT_ID, svc.short_title)
        out = env.last(CLIENT_ID)
        assert svc.title in out.text
        assert "Преимущества" in out.text and "⏱" in out.text
        texts = [b.text for b in env.buttons(out)]
        assert "🗓 Записаться на услугу" in texts and "⬅️ К каталогу" in texts


async def test_price_list_has_disclaimer_and_links_to_services(env):
    await env.send(CLIENT_ID, BTN_PRICES)
    out = env.last(CLIENT_ID)
    assert "Итоговая стоимость зависит" in out.text
    await env.click(CLIENT_ID, "Керамика")
    assert "Керамическое покрытие" in env.last_text(CLIENT_ID)


# -------------------------------------------------------------- калькулятор
async def test_calculator_full_flow_and_booking_from_it(env):
    await env.send(CLIENT_ID, BTN_CALC)
    await env.click(CLIENT_ID, "Полировка кузова")
    await env.click(CLIENT_ID, "Бизнес-класс")
    await env.click(CLIENT_ID, "Деконтаминация")
    await env.click(CLIENT_ID, "Рассчитать")
    text = env.last_text(CLIENT_ID)
    # 18 000 × 1,35 = 24 300 + 4 000 = 28 300
    assert "28 300" in text and "предварительн" in text.lower()
    assert "Деконтаминация" in text
    # переход в запись: услуга и класс уже выбраны
    await env.click(CLIENT_ID, "Оставить заявку")
    assert "марку и модель" in env.last_text(CLIENT_ID)
    assert "шаг 1 из 7" in env.last_text(CLIENT_ID)


async def test_calculator_restart_and_stale_buttons(env):
    await env.send(CLIENT_ID, BTN_CALC)
    old_out = env.last(CLIENT_ID)
    await env.click(CLIENT_ID, "Мойка кузова")
    await env.click(CLIENT_ID, "Компактный")
    await env.click(CLIENT_ID, "Рассчитать")
    await env.click(CLIENT_ID, "Рассчитать заново")
    assert "Шаг 1" in env.last_text(CLIENT_ID)
    # забытая кнопка выбора услуги работает как корректный выбор, а подделка отклоняется
    await env.press(CLIENT_ID, old_out, "calc:svc:no_such_service")
    assert "Шаг 1" in env.last_text(CLIENT_ID)


# ----------------------------------------------------------------- запись
async def test_booking_happy_path_saves_and_notifies_admin(env):
    await fill_booking(env)
    confirm = env.last_text(CLIENT_ID)
    assert "Проверьте заявку" in confirm and "BMW X5" in confirm and "+7 999 123-45-67" in confirm
    # без согласия отправить нельзя
    await env.click(CLIENT_ID, "Отправить заявку")
    assert await env.repo.count_bookings() == 0
    await env.click(CLIENT_ID, "Согласен")
    await env.click(CLIENT_ID, "Отправить заявку")

    assert await env.repo.count_bookings() == 1
    booking = await env.repo.get_booking(1)
    assert booking.status == BookingStatus.NEW and booking.consent and booking.consent_at
    assert booking.phone == "+79991234567" and booking.car == "BMW X5"
    assert booking.estimate_rub == 2300  # 1500 × 1,50, округление до сотен
    assert booking.start_at and booking.end_at and booking.car_id

    client_text = env.all_text(CLIENT_ID)
    assert "Заявка №1 отправлена" in client_text
    assert "Менеджер подтвердит запись" in client_text and "зарезервировано" in client_text

    admin_msg = env.last(ADMIN_ID)
    assert "Новая заявка №1" in admin_msg.text
    for needle in ("Иван", "+7 999 123-45-67", "BMW X5", "Мойка кузова", "Позвоните после 18:00",
                   str(CLIENT_ID), "@ivan"):
        assert needle in admin_msg.text
    labels = [b.text for b in env.buttons(admin_msg)]
    assert labels == ["✅ Принять в работу", "❌ Отклонить заявку", "📋 Подробнее"]


async def test_double_click_on_submit_creates_one_booking(env):
    await fill_booking(env)
    await env.click(CLIENT_ID, "Согласен")
    out, button = env.find_button(CLIENT_ID, "Отправить заявку")
    import asyncio
    await asyncio.gather(
        env.press(CLIENT_ID, out, button.callback_data),
        env.press(CLIENT_ID, out, button.callback_data),
        env.press(CLIENT_ID, out, button.callback_data),
    )
    assert await env.repo.count_bookings() == 1
    admin_new = [o for o in env.visible(ADMIN_ID) if "Новая заявка" in (o.text or "")]
    assert len(admin_new) == 1


async def test_submit_after_completion_is_rejected_as_stale(env):
    await fill_booking(env)
    await env.click(CLIENT_ID, "Согласен")
    out, button = env.find_button(CLIENT_ID, "Отправить заявку")
    await env.press(CLIENT_ID, out, button.callback_data)
    await env.press(CLIENT_ID, out, button.callback_data)  # повтор после очистки FSM
    assert await env.repo.count_bookings() == 1


async def test_back_button_walks_steps(env):
    await env.send(CLIENT_ID, BTN_BOOK)
    await env.click(CLIENT_ID, "Мойка кузова")
    await env.send(CLIENT_ID, "Audi A6")
    await env.click(CLIENT_ID, "Средний")
    assert "Выберите <b>дату</b>" in env.last_text(CLIENT_ID)
    await env.click(CLIENT_ID, "Назад")
    assert "класс автомобиля" in env.last_text(CLIENT_ID)
    await env.click(CLIENT_ID, "Назад")
    assert "марку и модель" in env.last_text(CLIENT_ID)
    await env.click(CLIENT_ID, "Назад")
    assert "Выберите" in env.last_text(CLIENT_ID) and "услугу" in env.last_text(CLIENT_ID)


async def test_text_back_on_phone_step_and_cancel(env):
    await env.send(CLIENT_ID, BTN_BOOK)
    await env.click(CLIENT_ID, "Мойка кузова")
    await env.send(CLIENT_ID, "Kia Rio")
    await env.click(CLIENT_ID, "Компактный")
    await env.click(CLIENT_ID, "Завтра")
    await env.click(CLIENT_ID, "12:00")
    await env.send(CLIENT_ID, "Мария")
    assert "номер телефона" in env.last_text(CLIENT_ID)
    await env.send(CLIENT_ID, "⬅️ Назад")
    assert "Как к вам обращаться" in env.last_text(CLIENT_ID)
    await env.send(CLIENT_ID, "/cancel")
    assert "отменено" in env.last_text(CLIENT_ID)
    assert await env.repo.count_bookings() == 0
    # после отмены FSM чист: произвольный текст не воспринимается как ответ на шаг
    await env.send(CLIENT_ID, "Мария")
    assert "Не совсем понял" in env.last_text(CLIENT_ID)


async def test_invalid_input_keeps_step_and_explains(env):
    await env.send(CLIENT_ID, BTN_BOOK)
    await env.click(CLIENT_ID, "Мойка кузова")
    await env.send(CLIENT_ID, "1")
    assert "Укажите марку" in env.last_text(CLIENT_ID)
    await env.send(CLIENT_ID, "Lada Vesta")
    await env.click(CLIENT_ID, "Компактный")
    await env.send(CLIENT_ID, "32.13")
    assert "распознать дату" in env.last_text(CLIENT_ID)
    await env.send(CLIENT_ID, "01.01.2001")
    assert "прошла" in env.last_text(CLIENT_ID)
    await env.send(CLIENT_ID, "завтра")
    await env.send(CLIENT_ID, "03:00")
    assert "работает" in env.last_text(CLIENT_ID)
    await env.send(CLIENT_ID, "15:30")                       # формат верный, но такого слота нет
    assert "недоступно" in env.last_text(CLIENT_ID)
    await env.send(CLIENT_ID, "15:00")
    await env.send(CLIENT_ID, "123")
    assert "только буквы" in env.last_text(CLIENT_ID)
    await env.send(CLIENT_ID, "Олег")
    await env.send(CLIENT_ID, "12345")
    assert "номер" in env.last_text(CLIENT_ID).lower()
    await env.send(CLIENT_ID, "+7 (999) 123-45-67")
    await env.send(CLIENT_ID, "x" * 600)
    assert "слишком длинный" in env.last_text(CLIENT_ID)


async def test_sticker_in_fsm_does_not_break_flow(env):
    await env.send(CLIENT_ID, BTN_BOOK)
    await env.click(CLIENT_ID, "Мойка кузова")
    await env.send_sticker(CLIENT_ID)
    assert "марку и модель" in env.last_text(CLIENT_ID)
    await env.send(CLIENT_ID, "/unknowncommand")
    assert "марку и модель" in env.last_text(CLIENT_ID)


async def test_contact_button_and_foreign_contact(env):
    await env.send(CLIENT_ID, BTN_BOOK)
    await env.click(CLIENT_ID, "Мойка кузова")
    await env.send(CLIENT_ID, "Kia Rio")
    await env.click(CLIENT_ID, "Компактный")
    await env.click(CLIENT_ID, "Завтра")
    await env.click(CLIENT_ID, "12:00")
    await env.click(CLIENT_ID, "Использовать «")
    assert "номер телефона" in env.last_text(CLIENT_ID)
    await env.send_contact(CLIENT_ID, "79990001122", contact_user_id=555)
    assert "свой контакт" in env.last_text(CLIENT_ID)
    await env.send_contact(CLIENT_ID, "79990001122")
    assert "комментарий" in env.last_text(CLIENT_ID).lower()
    await env.click(CLIENT_ID, "Пропустить")
    assert "Проверьте заявку" in env.last_text(CLIENT_ID)
    assert "+7 999 000-11-22" in env.last_text(CLIENT_ID)


async def test_policy_text_opens_and_returns(env):
    await fill_booking(env)
    await env.click(CLIENT_ID, "Читать согласие")
    assert "Согласие на обработку" in env.last_text(CLIENT_ID)
    await env.click(CLIENT_ID, "Вернуться к заявке")
    assert "Проверьте заявку" in env.last_text(CLIENT_ID)


async def test_menu_button_during_booking_leaves_cleanly(env):
    await env.send(CLIENT_ID, BTN_BOOK)
    await env.click(CLIENT_ID, "Мойка кузова")
    await env.send(CLIENT_ID, BTN_PRICES)
    assert "Прайс-лист" in env.last_text(CLIENT_ID)
    assert "прервано" in env.all_text(CLIENT_ID)
    await env.send(CLIENT_ID, "Просто текст")
    assert "Не совсем понял" in env.last_text(CLIENT_ID)


async def test_stale_booking_button_is_rejected_gracefully(env):
    await env.send(CLIENT_ID, BTN_BOOK)
    out, button = env.find_button(CLIENT_ID, "Мойка кузова")
    await env.send(CLIENT_ID, "/cancel")
    await env.press(CLIENT_ID, out, button.callback_data)  # state пуст
    assert await env.repo.count_bookings() == 0


async def test_book_from_service_card_skips_service_step(env):
    await env.send(CLIENT_ID, BTN_SERVICES)
    await env.click(CLIENT_ID, "Керамика")
    await env.click(CLIENT_ID, "Записаться на услугу")
    text = env.last_text(CLIENT_ID)
    assert "шаг 1 из 8" in text and "Керамическое покрытие" in text
    assert "Назад" not in [b.text for b in env.buttons(env.last(CLIENT_ID))]  # на первом шаге «Назад» нет


# ---------------------------------------------------------- админ-панель
async def make_booking(env, uid=CLIENT_ID):
    await fill_booking(env, uid)
    await env.click(uid, "Согласен")
    await env.click(uid, "Отправить заявку")


async def test_admin_accept_notifies_client_and_updates_status(env):
    await make_booking(env)
    await env.click(ADMIN_ID, "Принять в работу", username="boss")
    booking = await env.repo.get_booking(1)
    assert booking.status == BookingStatus.IN_WORK
    client_msg = env.last_text(CLIENT_ID)
    assert "принята в работу" in client_msg
    assert "подтверждённой только после" in client_msg  # не называем запись окончательной
    # повторное нажатие на ту же кнопку безопасно
    sent_before = len(env.visible(CLIENT_ID))
    out, button = env.find_button(ADMIN_ID, "Принять в работу") if any(
        "Принять в работу" in b.text for o in env.visible(ADMIN_ID) for b in env.buttons(o)) else (None, None)
    if out is not None:
        await env.press(ADMIN_ID, out, button.callback_data)
    assert len(env.visible(CLIENT_ID)) == sent_before
    assert (await env.repo.status_history(1))[-1][1] == "in_work"


async def test_admin_reject_and_change_from_panel(env):
    await make_booking(env)
    await env.click(ADMIN_ID, "Отклонить заявку")
    assert (await env.repo.get_booking(1)).status == BookingStatus.REJECTED
    assert "не получится" in env.last_text(CLIENT_ID)
    # из карточки можно перевести в «Завершена»
    await env.click(ADMIN_ID, "Завершена")
    assert (await env.repo.get_booking(1)).status == BookingStatus.DONE
    assert "завершена" in env.last_text(CLIENT_ID)


async def test_admin_panel_lists_stats_pagination_and_csv(env):
    for i in range(10):
        await env.repo.upsert_user(2000 + i, f"u{i}", f"U{i}")
        from database.models import NewBooking
        await env.repo.create_booking(NewBooking(
            draft_id=f"d{i}", user_id=2000 + i, service_id="wash", service_title="Мойка",
            car="Kia Rio", car_class_id="compact", car_class_title="Компактный",
            visit_date="2030-01-01", visit_time="12:00", name=f"Клиент{i}", phone="+79990000000", consent=True))
    await env.send(ADMIN_ID, "/admin")
    assert "Новых заявок: <b>10</b>" in env.last_text(ADMIN_ID)
    await env.click(ADMIN_ID, "Все заявки")
    assert "страница 1 из 2" in env.last_text(ADMIN_ID)
    await env.click(ADMIN_ID, "▶️")
    assert "страница 2 из 2" in env.last_text(ADMIN_ID)
    await env.click(ADMIN_ID, "№1 ")
    assert "Заявка №1" in env.last_text(ADMIN_ID)
    await env.click(ADMIN_ID, "Панель")
    await env.click(ADMIN_ID, "Последние 10")
    assert "Последние 10" in env.last_text(ADMIN_ID)
    await env.click(ADMIN_ID, "Панель")
    await env.click(ADMIN_ID, "Статистика")
    assert "Всего заявок: <b>10</b>" in env.last_text(ADMIN_ID)
    await env.click(ADMIN_ID, "Панель")
    await env.click(ADMIN_ID, "CSV")
    doc = next(o for o in env.tg.outbox if o.kind == "SendDocument")
    assert doc.filename.endswith(".csv") and "Клиент3".encode() in doc.document


async def test_non_admin_cannot_use_admin_panel_or_buttons(env):
    await make_booking(env)
    await env.send(CLIENT_ID, "/admin")
    assert "нет доступа" in env.last_text(CLIENT_ID)
    # подделанная кнопка администратора от обычного пользователя
    out = env.last(ADMIN_ID)
    approve = next(b for b in env.buttons(out) if "Принять" in b.text)
    await env.press(CLIENT_ID, out, approve.callback_data)
    assert (await env.repo.get_booking(1)).status == BookingStatus.NEW
    await env.press(CLIENT_ID, out, "adm:csv:0:::0")
    assert not [o for o in env.tg.outbox if o.kind == "SendDocument"]


async def test_admin_blocked_by_client_shows_warning_not_crash(env):
    await make_booking(env)
    env.tg.forbidden_chats.add(CLIENT_ID)
    await env.click(ADMIN_ID, "Принять в работу")
    assert (await env.repo.get_booking(1)).status == BookingStatus.IN_WORK  # статус всё равно сохранён


# ------------------------------------------------------ контакты, портфолио
async def test_contacts_hide_unconfigured_links_and_manager_fallback(env):
    await env.send(CLIENT_ID, BTN_CONTACTS)
    out = env.last(CLIENT_ID)
    assert not [b for b in env.buttons(out) if b.url]
    assert "демо" in out.text
    await env.send(CLIENT_ID, BTN_MANAGER)
    out = env.last(CLIENT_ID)
    assert "не настроен" in out.text and any("Оставить заявку" in b.text for b in env.buttons(out))


async def test_configured_contacts_show_working_links(env):
    env.settings = replace(env.settings, studio=replace(
        env.settings.studio, manager_username="detail_manager",
        map_url="https://maps.example.com/x", website_url="https://example.com"))
    env.dp.workflow_data["settings"] = env.settings
    await env.send(CLIENT_ID, BTN_CONTACTS)
    urls = {b.url for b in env.buttons(env.last(CLIENT_ID)) if b.url}
    assert urls == {"https://t.me/detail_manager", "https://maps.example.com/x", "https://example.com"}
    await env.send(CLIENT_ID, BTN_MANAGER)
    assert any(b.url == "https://t.me/detail_manager" for b in env.buttons(env.last(CLIENT_ID)))


async def test_portfolio_text_card_without_photos_and_with_photo(env, tmp_path):
    await env.send(CLIENT_ID, BTN_PORTFOLIO)
    await env.click(CLIENT_ID, "Керамическое покрытие")
    text = env.last_text(CLIENT_ID)
    assert "Демо-карточка" in text and "Выполненные работы" in text
    assert not [o for o in env.tg.outbox if o.kind == "SendPhoto"]

    env.settings.portfolio_dir.mkdir(parents=True, exist_ok=True)
    (env.settings.portfolio_dir / "polish.jpg").write_bytes(b"\xff\xd8\xff fake")
    await env.click(CLIENT_ID, "К портфолио")
    await env.click(CLIENT_ID, "Полировка кузова")
    photo = [o for o in env.tg.outbox if o.kind == "SendPhoto"]
    assert len(photo) == 1 and "Полировка кузова" in photo[0].text


async def test_faq_answers(env):
    from content.faq import FAQ
    for item in FAQ:
        await env.send(CLIENT_ID, BTN_FAQ)
        await env.click(CLIENT_ID, item.question)
        assert item.answer[:30] in env.last_text(CLIENT_ID)


async def test_new_request_button_keeps_status_notification(env):
    await make_booking(env)
    await env.click(ADMIN_ID, "Отклонить заявку")
    notification = next(o for o in env.visible(CLIENT_ID) if "не получится" in (o.text or ""))
    await env.click(CLIENT_ID, "Новая заявка")
    assert "не получится" in notification.text            # уведомление не затёрто
    assert "шаг 1 из 9" in env.last_text(CLIENT_ID)       # а мастер записи открылся новым сообщением


async def test_today_is_hidden_when_no_slots_left(env, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import services.availability as availability_mod
    late = datetime(2026, 10, 12, 21, 30, tzinfo=ZoneInfo("Europe/Moscow"))
    monkeypatch.setattr(availability_mod, "local_now", lambda studio, now=None: late.replace(tzinfo=None))
    await env.send(CLIENT_ID, BTN_BOOK)
    await env.click(CLIENT_ID, "Мойка кузова")
    await env.send(CLIENT_ID, "Kia Rio")
    await env.click(CLIENT_ID, "Компактный")
    labels = [b.text for b in env.buttons(env.last(CLIENT_ID))]
    assert "Сегодня" not in labels and "Завтра" in labels
