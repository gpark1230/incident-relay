from rq import Queue
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.listener import handle_event
from app.models import NotificationAttempt


def _make_session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def test_handle_event_records_attempt_and_enqueues_job(mocker, fake_redis):
    session_factory = _make_session_factory()
    mocker.patch("app.listener.SessionLocal", session_factory)

    queue = Queue("notifications", connection=fake_redis)

    event = {
        "event": "incident.created",
        "incident_id": 5,
        "user_id": 3,
        "details": "severity: critical",
        "timestamp": "2026-08-13T18:00:00+00:00",
    }

    handle_event(event, queue)

    session = session_factory()
    attempts = session.execute(select(NotificationAttempt)).scalars().all()
    assert len(attempts) == 1
    assert attempts[0].incident_id == 5
    assert attempts[0].recipient == "user:3"

    assert len(queue) == 1


def test_handle_event_skips_unrecognized_event_type(mocker, fake_redis):
    session_factory = _make_session_factory()
    mocker.patch("app.listener.SessionLocal", session_factory)
    queue = Queue("notifications", connection=fake_redis)

    handle_event({"event": "incident.deleted", "incident_id": 1, "user_id": 1}, queue)

    session = session_factory()
    attempts = session.execute(select(NotificationAttempt)).scalars().all()
    assert len(attempts) == 0
    assert len(queue) == 0
