import asyncio
import csv
import io

import pytest
import pytest_asyncio

from database import BookingStatus, Database, NewBooking, Repository
from services.admin import AdminService, PermissionDenied
from services.export import CSV_COLUMNS, bookings_to_csv

pytestmark = pytest.mark.asyncio


def nb(draft="d1", user=10, **over) -> NewBooking:
    base = dict(draft_id=draft, user_id=user, service_id="polish", service_title="Полировка кузова",
                car="BMW X5", car_class_id="suv", car_class_title="Внедорожник",
                visit_date="2026-10-20", visit_time="12:00", name="Иван", phone="+79991234567",
                comment="Тест", estimate_rub=27000, addons=("Деконтаминация",), consent=True)
    base.update(over)
    return NewBooking(**base)


@pytest_asyncio.fixture
async def repo(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    r = Repository(db)
    await r.upsert_user(10, "ivan", "Иван П")
    await r.upsert_user(11, None, "Мария")
    yield r
    await db.close()


async def test_tables_created_automatically(tmp_path):
    db = Database(tmp_path / "sub" / "dir" / "new.db")
    await db.connect()
    async with db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'") as cur:
        tables = {r[0] for r in await cur.fetchall()}
    assert {"users", "bookings", "booking_status_log"} <= tables
    await db.close()


async def test_create_booking_assigns_unique_ids_and_persists_after_reopen(tmp_path):
    path = tmp_path / "p.db"
    db = Database(path); await db.connect(); r = Repository(db)
    await r.upsert_user(10, "ivan", "Иван")
    b1, created1 = await r.create_booking(nb("a"))
    b2, created2 = await r.create_booking(nb("b"))
    assert created1 and created2 and (b1.id, b2.id) == (1, 2)
    assert b1.status == BookingStatus.NEW and b1.consent and b1.consent_at
    assert b1.addons == ("Деконтаминация",) and b1.username == "ivan"
    await db.close()

    db2 = Database(path); await db2.connect(); r2 = Repository(db2)
    again = await r2.get_booking(1)
    assert again is not None and again.name == "Иван" and again.estimate_rub == 27000
    await db2.close()


async def test_same_draft_is_not_saved_twice(repo):
    first, c1 = await repo.create_booking(nb("same"))
    second, c2 = await repo.create_booking(nb("same"))
    assert c1 and not c2 and first.id == second.id
    assert await repo.count_bookings() == 1


async def test_concurrent_submissions_create_single_row(repo):
    results = await asyncio.gather(*[repo.create_booking(nb("race")) for _ in range(10)])
    assert sum(1 for _, created in results if created) == 1
    assert await repo.count_bookings() == 1


async def test_sql_injection_is_stored_as_plain_text(repo):
    evil = "Robert'); DROP TABLE bookings;--"
    b, _ = await repo.create_booking(nb("evil", comment=evil, name="Bob"))
    assert (await repo.get_booking(b.id)).comment == evil
    assert await repo.count_bookings() == 1


async def test_status_update_history_and_compare_and_swap(repo):
    b, _ = await repo.create_booking(nb())
    res = await repo.set_status(b.id, BookingStatus.IN_WORK, actor_id=900, expected=BookingStatus.NEW)
    assert res.changed and res.booking.status == BookingStatus.IN_WORK and res.old_status == BookingStatus.NEW
    assert res.booking.updated_at >= b.updated_at

    # повтор и «опоздавший» админ не меняют ничего
    again = await repo.set_status(b.id, BookingStatus.IN_WORK, actor_id=900, expected=BookingStatus.NEW)
    late = await repo.set_status(b.id, BookingStatus.REJECTED, actor_id=901, expected=BookingStatus.NEW)
    assert not again.changed and not late.changed
    assert (await repo.get_booking(b.id)).status == BookingStatus.IN_WORK

    done = await repo.set_status(b.id, BookingStatus.DONE, actor_id=900)
    assert done.changed
    history = await repo.status_history(b.id)
    assert [h[1] for h in history] == ["new", "in_work", "done"]
    assert history[1][2] == 900 and history[0][2] is None

    missing = await repo.set_status(999, BookingStatus.DONE, actor_id=900)
    assert missing.booking is None and not missing.changed


async def test_concurrent_status_clicks_apply_once(repo):
    b, _ = await repo.create_booking(nb())
    results = await asyncio.gather(*[
        repo.set_status(b.id, BookingStatus.IN_WORK, actor_id=900, expected=BookingStatus.NEW) for _ in range(8)])
    assert sum(r.changed for r in results) == 1
    assert len(await repo.status_history(b.id)) == 2


async def test_stats(repo):
    ids = []
    for i, svc in enumerate(["polish", "polish", "wash", "wash", "wash", "ppf"]):
        b, _ = await repo.create_booking(nb(f"s{i}", service_id=svc, service_title=svc.upper(), user_id=10 + i % 2))
        ids.append(b.id)
    await repo.set_status(ids[0], BookingStatus.IN_WORK, actor_id=1)
    await repo.set_status(ids[1], BookingStatus.ACCEPTED, actor_id=1)
    await repo.set_status(ids[2], BookingStatus.REJECTED, actor_id=1)
    await repo.set_status(ids[3], BookingStatus.DONE, actor_id=1)
    st = await repo.stats()
    assert st.total == 6 and st.new == 2 and st.rejected == 1
    assert st.accepted == 3  # в работе + принята + завершена
    assert st.by_status[BookingStatus.DONE] == 1
    assert st.users == 2 and st.last_7_days == 6
    assert st.top_services[0] == ("WASH", 3)


async def test_listing_pagination_and_filters(repo):
    for i in range(25):
        await repo.create_booking(nb(f"l{i}"))
    await repo.set_status(25, BookingStatus.REJECTED, actor_id=1)
    page1 = await repo.list_bookings(limit=10, offset=0)
    page3 = await repo.list_bookings(limit=10, offset=20)
    assert [b.id for b in page1] == list(range(25, 15, -1))   # новые сверху
    assert len(page3) == 5
    assert [b.id for b in await repo.list_bookings(BookingStatus.REJECTED)] == [25]
    assert await repo.count_bookings(BookingStatus.NEW) == 24


# ------------------------------------------------------------------- CSV
async def test_csv_export_contents_and_format(repo):
    await repo.create_booking(nb("c1", name="Иван", comment="=HYPERLINK(\"http://evil\")", car="+cmd"))
    await repo.create_booking(nb("c2", user=11, name="Мария; \"Кавычки\"", comment="строка\nвторая"))
    data = bookings_to_csv(await repo.iter_all_bookings())
    assert data.startswith(b"\xef\xbb\xbf")  # BOM для Excel
    rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig")), delimiter=";"))
    assert tuple(rows[0]) == CSV_COLUMNS and len(rows) == 3
    idx = {name: i for i, name in enumerate(CSV_COLUMNS)}
    assert rows[1][idx["Номер"]] == "1" and rows[1][idx["Статус"]] == "🆕 Новая"
    assert rows[1][idx["Телефон"]] == "+79991234567" and rows[1][idx["Username"]] == "@ivan"
    assert rows[1][idx["Предв. стоимость, ₽"]] == "27000"
    assert rows[1][idx["Комментарий"]].startswith("'=")      # защита от формул
    assert rows[1][idx["Автомобиль"]].startswith("'+")
    assert rows[2][idx["Имя"]] == 'Мария; "Кавычки"'          # спецсимволы экранированы корректно
    assert rows[2][idx["Комментарий"]] == "строка\nвторая"


async def test_csv_of_empty_db_has_only_header(repo):
    rows = list(csv.reader(io.StringIO(bookings_to_csv([]).decode("utf-8-sig")), delimiter=";"))
    assert len(rows) == 1


# --------------------------------------------------- права администратора
async def test_admin_service_denies_strangers_for_every_operation(repo):
    b, _ = await repo.create_booking(nb())
    admin = AdminService(repo, {900})
    stranger = 12345
    calls = [
        admin.new_count(stranger), admin.total_count(stranger),
        admin.list_page(stranger, None, 0), admin.last(stranger), admin.get(stranger, b.id),
        admin.history(stranger, b.id), admin.stats(stranger), admin.export_csv(stranger),
        admin.change_status(stranger, b.id, BookingStatus.DONE),
        admin.new_count(None), admin.change_status(None, b.id, BookingStatus.DONE),
    ]
    for call in calls:
        with pytest.raises(PermissionDenied):
            await call
    assert (await repo.get_booking(b.id)).status == BookingStatus.NEW


async def test_admin_service_allows_admins_and_paginates(repo):
    for i in range(19):
        await repo.create_booking(nb(f"p{i}"))
    admin = AdminService(repo, [900, 901])
    assert admin.is_admin(901) and not admin.is_admin(1) and not admin.is_admin(None)
    page = await admin.list_page(900, None, 1, page_size=8)
    assert (page.page, page.pages, page.total, len(page.items)) == (1, 3, 19, 8)
    assert (await admin.list_page(900, None, 99, page_size=8)).page == 2  # страница ограничивается
    assert (await admin.list_page(900, None, -5, page_size=8)).page == 0
    assert len(await admin.last(900)) == 10
    res = await admin.change_status(900, 1, BookingStatus.ACCEPTED)
    assert res.changed
