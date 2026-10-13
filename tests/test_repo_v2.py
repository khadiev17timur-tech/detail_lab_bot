import asyncio
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from database import BookingStatus, Database, NewBooking, Repository
from database.errors import NotAllowed, SlotTaken, VoucherUnavailable
from database.repository import iso_utc, utcnow
from services.schedule import from_iso, to_iso

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


def nb(draft="d1", user=10, start="2026-10-20T12:00", end="2026-10-20T13:00", **over) -> NewBooking:
    base = dict(draft_id=draft, user_id=user, service_id="wash", service_title="Мойка", car="BMW X5",
                car_class_id="suv", car_class_title="Внедорожник", visit_date=start[:10], visit_time=start[11:],
                name="Иван", phone="+79990000000", estimate_rub=2300, consent=True, start_at=start, end_at=end)
    base.update(over)
    return NewBooking(**base)


@pytest_asyncio.fixture
async def repo(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    r = Repository(db)
    for uid in (10, 11, 12):
        await r.upsert_user(uid, f"u{uid}", f"U{uid}")
    yield r
    await db.close()


# ------------------------------------------------------------ двойное бронирование
async def test_second_booking_of_last_post_is_rejected(repo):
    await repo.create_booking(nb("a"), posts=1)
    with pytest.raises(SlotTaken):
        await repo.create_booking(nb("b", user=11), posts=1)
    assert await repo.count_bookings() == 1


async def test_two_posts_allow_two_but_not_three(repo):
    await repo.create_booking(nb("a"), posts=2)
    await repo.create_booking(nb("b", user=11), posts=2)
    with pytest.raises(SlotTaken):
        await repo.create_booking(nb("c", user=12), posts=2)


async def test_race_for_last_slot_only_one_wins(repo):
    results = await asyncio.gather(
        *[repo.create_booking(nb(f"r{i}", user=10 + i % 3), posts=1) for i in range(12)], return_exceptions=True)
    ok = [r for r in results if not isinstance(r, Exception)]
    taken = [r for r in results if isinstance(r, SlotTaken)]
    assert len(ok) == 1 and len(taken) == 11
    assert await repo.count_bookings() == 1


async def test_same_draft_resubmitted_is_idempotent_even_when_slot_now_busy(repo):
    first, c1 = await repo.create_booking(nb("same"), posts=1)
    again, c2 = await repo.create_booking(nb("same"), posts=1)       # свой же слот не должен давать «занято»
    assert c1 and not c2 and first.id == again.id


async def test_cancelled_rejected_done_free_the_slot(repo):
    for status in (BookingStatus.CANCELLED, BookingStatus.REJECTED, BookingStatus.DONE):
        b, _ = await repo.create_booking(nb(f"d-{status.value}"), posts=1)
        with pytest.raises(SlotTaken):
            await repo.create_booking(nb(f"x-{status.value}", user=11), posts=1)
        await repo.set_status(b.id, status, actor_id=900)
        again, created = await repo.create_booking(nb(f"y-{status.value}", user=11), posts=1)
        assert created
        await repo.set_status(again.id, BookingStatus.REJECTED, actor_id=900)


async def test_overlap_by_partial_interval_is_a_conflict(repo):
    await repo.create_booking(nb("a", start="2026-10-20T12:00", end="2026-10-20T14:00"), posts=1)
    with pytest.raises(SlotTaken):
        await repo.create_booking(nb("b", user=11, start="2026-10-20T13:00", end="2026-10-20T14:00"), posts=1)
    ok, _ = await repo.create_booking(nb("c", user=11, start="2026-10-20T14:00", end="2026-10-20T15:00"), posts=1)
    assert ok.id


# ------------------------------------------------------------------- перенос
async def test_reschedule_moves_booking_and_requires_reconfirmation(repo):
    b, _ = await repo.create_booking(nb("a"), posts=1, jobs=[("remind:24", "2026-10-19T12:00:00+00:00")])
    await repo.set_status(b.id, BookingStatus.ACCEPTED, actor_id=900)
    moved = await repo.reschedule(b.id, 10, from_iso("2026-10-21T15:00"), from_iso("2026-10-21T16:00"), posts=1,
                                  jobs=[("remind:24", "2026-10-20T15:00:00+00:00")])
    assert (moved.start_at, moved.visit_date, moved.visit_time) == ("2026-10-21T15:00", "2026-10-21", "15:00")
    assert moved.status == BookingStatus.NEW and moved.reschedule_count == 1 and moved.client_confirmed_at is None
    jobs = await repo.jobs_for_booking(b.id)
    assert [(j.kind, j.status, j.due_at[:10]) for j in jobs] == [("remind:24", "pending", "2026-10-20")]
    # старое время освободилось
    ok, _ = await repo.create_booking(nb("b", user=11), posts=1)
    assert ok.id


async def test_reschedule_to_taken_slot_or_foreign_booking_is_refused(repo):
    a, _ = await repo.create_booking(nb("a"), posts=1)
    await repo.create_booking(nb("b", user=11, start="2026-10-21T15:00", end="2026-10-21T16:00"), posts=1)
    with pytest.raises(SlotTaken):
        await repo.reschedule(a.id, 10, from_iso("2026-10-21T15:00"), from_iso("2026-10-21T16:00"), posts=1)
    with pytest.raises(NotAllowed):
        await repo.reschedule(a.id, 11, from_iso("2026-10-22T10:00"), from_iso("2026-10-22T11:00"), posts=1)
    assert (await repo.get_booking(a.id)).start_at == "2026-10-20T12:00"          # ничего не изменилось


async def test_reschedule_within_own_interval_is_allowed(repo):
    a, _ = await repo.create_booking(nb("a", start="2026-10-20T12:00", end="2026-10-20T14:00"), posts=1)
    moved = await repo.reschedule(a.id, 10, from_iso("2026-10-20T13:00"), from_iso("2026-10-20T15:00"), posts=1)
    assert moved.start_at == "2026-10-20T13:00"


async def test_cannot_reschedule_finished_booking(repo):
    a, _ = await repo.create_booking(nb("a"), posts=1)
    await repo.set_status(a.id, BookingStatus.DONE, actor_id=900)
    with pytest.raises(NotAllowed):
        await repo.reschedule(a.id, 10, from_iso("2026-10-22T10:00"), from_iso("2026-10-22T11:00"), posts=1)


# --------------------------------------------------------------------- купоны
async def make_rule(repo, **over):
    kw = dict(title="−10%", kind="percent", value=10, trigger="visits", threshold=1, valid_days=30, per_client_limit=1)
    kw.update(over)
    return await repo.create_rule(**kw)


async def test_voucher_is_reserved_on_booking_released_on_cancel_used_on_done(repo):
    rule = await make_rule(repo)
    v = await repo.issue_voucher(rule, 10, NOW)
    b, _ = await repo.create_booking(nb("a", voucher_id=v.id, discount_rub=230), posts=2)
    assert (await repo.get_voucher(v.id)).status == "reserved" and b.discount_rub == 230
    await repo.set_status(b.id, BookingStatus.CANCELLED, actor_id=10, cancelled_by="client")
    assert (await repo.get_voucher(v.id)).status == "active"                       # вернулся клиенту
    b2, _ = await repo.create_booking(nb("b", voucher_id=v.id, start="2026-10-22T12:00", end="2026-10-22T13:00"), posts=2)
    await repo.set_status(b2.id, BookingStatus.DONE, actor_id=900)
    assert (await repo.get_voucher(v.id)).status == "used"
    with pytest.raises(VoucherUnavailable):                                         # повторно применить нельзя
        await repo.create_booking(nb("c", voucher_id=v.id, start="2026-10-23T12:00", end="2026-10-23T13:00"), posts=2)


async def test_voucher_cannot_be_used_twice_in_parallel(repo):
    v = await repo.issue_voucher(await make_rule(repo), 10, NOW)
    results = await asyncio.gather(*[
        repo.create_booking(nb(f"p{i}", voucher_id=v.id, start=f"2026-10-2{i}T12:00", end=f"2026-10-2{i}T13:00"), posts=3)
        for i in range(5)], return_exceptions=True)
    assert sum(not isinstance(r, Exception) for r in results) == 1
    assert sum(isinstance(r, VoucherUnavailable) for r in results) == 4


async def test_foreign_or_expired_voucher_is_unavailable(repo):
    rule = await make_rule(repo)
    v = await repo.issue_voucher(rule, 10, NOW)
    with pytest.raises(VoucherUnavailable):
        await repo.create_booking(nb("a", user=11, voucher_id=v.id), posts=2)
    old = await repo.issue_voucher(await make_rule(repo, title="old"), 10, datetime(2020, 1, 1, tzinfo=timezone.utc))
    with pytest.raises(VoucherUnavailable):
        await repo.create_booking(nb("b", voucher_id=old.id), posts=2)
    assert await repo.count_bookings() == 0


async def test_per_client_limit_blocks_repeat_issue(repo):
    rule = await make_rule(repo, per_client_limit=2)
    assert await repo.issue_voucher(rule, 10, NOW)
    assert await repo.issue_voucher(rule, 10, NOW)
    assert await repo.issue_voucher(rule, 10, NOW) is None
    assert await repo.issue_voucher(rule, 11, NOW)                                   # у другого клиента свой лимит


async def test_parallel_issue_respects_limit(repo):
    rule = await make_rule(repo, per_client_limit=1)
    res = await asyncio.gather(*[repo.issue_voucher(rule, 10, NOW) for _ in range(8)])
    assert sum(v is not None for v in res) == 1


# ------------------------------------------------------------------ задания
async def test_claim_due_jobs_is_exclusive_and_idempotent(repo):
    b, _ = await repo.create_booking(nb("a"), posts=1, jobs=[("remind:24", "2026-10-01T00:00:00+00:00"),
                                                              ("remind:2", "2099-01-01T00:00:00+00:00")])
    await repo.add_jobs(b.id, 10, [("remind:24", "2026-10-01T00:00:00+00:00")])      # дубль игнорируется
    assert len(await repo.jobs_for_booking(b.id)) == 2
    first, second = await asyncio.gather(repo.claim_due_jobs(utcnow()), repo.claim_due_jobs(utcnow()))
    claimed = first + second
    assert [j.kind for j in claimed] == ["remind:24"]                                # будущее не взято, повтора нет
    assert await repo.claim_due_jobs(utcnow()) == []


async def test_stuck_jobs_are_failed_not_resent(repo):
    b, _ = await repo.create_booking(nb("a"), posts=1, jobs=[("review", "2026-10-01T00:00:00+00:00")])
    await repo.claim_due_jobs(utcnow())                                              # «отправили», процесс упал
    assert await repo.recover_stuck_jobs() == 1
    assert (await repo.jobs_for_booking(b.id))[0].status == "failed"
    assert await repo.claim_due_jobs(utcnow()) == []


async def test_cancel_cancels_pending_reminders_and_done_creates_followups(repo):
    b, _ = await repo.create_booking(nb("a"), posts=2, jobs=[("remind:24", "2099-01-01T00:00:00+00:00")])
    await repo.set_status(b.id, BookingStatus.CANCELLED, actor_id=10, cancelled_by="client")
    assert (await repo.jobs_for_booking(b.id))[0].status == "cancelled"
    assert (await repo.get_booking(b.id)).cancelled_by == "client"
    c, _ = await repo.create_booking(nb("b", start="2026-10-22T12:00", end="2026-10-22T13:00"), posts=2)
    res = await repo.set_status(c.id, BookingStatus.DONE, actor_id=900, jobs=[("review", "2026-10-22T15:00:00+00:00")])
    assert res.changed and [(j.kind, j.status) for j in await repo.jobs_for_booking(c.id)] == [("review", "pending")]
    again = await repo.set_status(c.id, BookingStatus.DONE, actor_id=900, jobs=[("review", "2026-10-22T16:00:00+00:00")])
    assert not again.changed and len(await repo.jobs_for_booking(c.id)) == 1


# -------------------------------------------------------------------- отзывы
async def test_review_only_for_done_booking_and_only_once(repo):
    b, _ = await repo.create_booking(nb("a"), posts=1)
    assert await repo.create_review(b.id, 10, 5) is None                             # визит ещё не завершён
    await repo.set_status(b.id, BookingStatus.DONE, actor_id=900)
    assert await repo.create_review(b.id, 11, 5) is None                             # чужая запись
    r = await repo.create_review(b.id, 10, 2, "долго")
    assert r and r.rating == 2 and r.comment == "долго"
    assert await repo.create_review(b.id, 10, 5) is None                             # повторная оценка отклонена
    assert (await repo.get_review(b.id)).rating == 2
    assert not await repo.add_review_comment(b.id, 10, "ещё")                        # комментарий уже есть
    with pytest.raises(ValueError):
        await repo.create_review(b.id, 10, 6)


async def test_review_comment_can_be_added_once_after_rating(repo):
    b, _ = await repo.create_booking(nb("a"), posts=1)
    await repo.set_status(b.id, BookingStatus.DONE, actor_id=900)
    await repo.create_review(b.id, 10, 4)
    assert await repo.add_review_comment(b.id, 10, "хорошо")
    assert not await repo.add_review_comment(b.id, 10, "подмена")
    assert (await repo.get_review(b.id)).comment == "хорошо"
    assert await repo.review_summary() == (1, 4.0)


# ---------------------------------------------------------------- авто и блоки
async def test_cars_are_deduplicated_per_user_and_linked(repo):
    a, _ = await repo.create_booking(nb("a", car="BMW  X5"), posts=2)
    b, _ = await repo.create_booking(nb("b", car="bmw x5", start="2026-10-22T12:00", end="2026-10-22T13:00"), posts=2)
    c, _ = await repo.create_booking(nb("c", user=11, car="BMW X5", start="2026-10-23T12:00", end="2026-10-23T13:00"), posts=2)
    assert a.car_id == b.car_id and c.car_id != a.car_id
    assert [x.title for x in await repo.list_cars(10)] == ["BMW  X5"]
    assert await repo.archive_car(a.car_id, 10) and not await repo.archive_car(a.car_id, 11)
    assert await repo.list_cars(10) == []


async def test_upcoming_and_day_views(repo):
    a, _ = await repo.create_booking(nb("a"), posts=2)
    old, _ = await repo.create_booking(nb("o", start="2026-10-01T12:00", end="2026-10-01T13:00"), posts=2)
    up = await repo.upcoming_for_user(10, datetime(2026, 10, 12, 9, 0))
    assert [b.id for b in up] == [a.id] and old.id not in [b.id for b in up]
    day = await repo.bookings_on_day(datetime(2026, 10, 20).date())
    assert [b.id for b in day] == [a.id]


async def test_schedule_blocks_roundtrip(repo):
    from datetime import date
    blk = await repo.add_block(date(2026, 11, 1), date(2026, 11, 3), "праздники")
    assert await repo.block_ranges(date(2026, 10, 1)) == [(date(2026, 11, 1), date(2026, 11, 3))]
    assert await repo.block_ranges(date(2026, 12, 1)) == []
    assert await repo.remove_block(blk.id) and not await repo.remove_block(blk.id)
