import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from database import BookingStatus, NewBooking
from services import availability
from services.scheduler import scheduler_loop
from tests.conftest import ADMIN_ID, CLIENT_ID

pytestmark = pytest.mark.asyncio


async def test_loop_sends_due_reminder_recovers_stuck_jobs_and_stops_cleanly(env):
    studio = env.settings.studio
    now = datetime.now(timezone.utc)
    start = (availability.local_now(studio, now) + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    await env.repo.upsert_user(CLIENT_ID, "u", "U")
    due = (now - timedelta(minutes=1)).isoformat(timespec="seconds")          # напоминание уже наступило
    stuck_due = (now - timedelta(minutes=2)).isoformat(timespec="seconds")
    b, _ = await env.repo.create_booking(NewBooking(
        draft_id="loop", user_id=CLIENT_ID, service_id="wash", service_title="Мойка", car="BMW", car_class_id="suv",
        car_class_title="Внедорожник", visit_date=start.date().isoformat(), visit_time=start.strftime("%H:%M"), name="И",
        phone="+79990000001", consent=True, start_at=start.strftime("%Y-%m-%dT%H:%M"),
        end_at=(start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")),
        posts=5, jobs=[("remind:2", due), ("remind:24", stuck_due)])
    await env.repo.set_status(b.id, BookingStatus.ACCEPTED, actor_id=ADMIN_ID)
    jobs = await env.repo.jobs_for_booking(b.id)
    stuck = next(j for j in jobs if j.kind == "remind:24")
    await env.repo._conn.execute("UPDATE jobs SET status='sending' WHERE id=?", (stuck.id,))   # «упали посреди отправки»
    await env.repo._conn.commit()

    task = asyncio.create_task(scheduler_loop(env.bot, env.repo, env.settings, interval=0.02))
    await asyncio.sleep(0.4)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    by_kind = {j.kind: j.status for j in await env.repo.jobs_for_booking(b.id)}
    assert by_kind["remind:2"] == "sent"
    assert by_kind["remind:24"] == "failed"                                  # прерванное не отправлено повторно
    assert len([o for o in env.visible(CLIENT_ID) if "Напоминаем" in (o.text or "")]) == 1


async def test_loop_survives_unexpected_errors(env, monkeypatch):
    import services.scheduler as sched
    calls = {"n": 0}
    real = sched.process_jobs

    async def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("временный сбой базы")
        return await real(*a, **kw)

    monkeypatch.setattr(sched, "process_jobs", flaky)
    task = asyncio.create_task(sched.scheduler_loop(env.bot, env.repo, env.settings, interval=0.02))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls["n"] >= 2                                                    # после ошибки цикл продолжился
