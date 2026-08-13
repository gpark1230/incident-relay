from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import NotificationAttempt, NotificationStatus
from app.tasks import process_notification

EVENT = {
    "event": "incident.created",
    "incident_id": 5,
    "user_id": 3,
    "details": "severity: critical",
    "timestamp": "2026-08-13T18:00:00+00:00",
}


def _make_session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def test_process_notification_marks_sent_on_success(mocker):
    session_factory = _make_session_factory()
    mocker.patch("app.tasks.SessionLocal", session_factory)

    session = session_factory()
    attempt = NotificationAttempt(
        event="incident.created", incident_id=5, recipient="user:3",
        status=NotificationStatus.pending,
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    attempt_id = attempt.id
    session.close()

    mocker.patch("app.tasks.send_notification", return_value=None)

    process_notification(attempt_id, EVENT)

    check_session = session_factory()
    result = check_session.get(NotificationAttempt, attempt_id)
    assert result.status == NotificationStatus.sent
    assert result.error_message is None


def test_process_notification_marks_failed_and_reraises(mocker):
    session_factory = _make_session_factory()
    mocker.patch("app.tasks.SessionLocal", session_factory)

    session = session_factory()
    attempt = NotificationAttempt(
        event="incident.created", incident_id=5, recipient="user:3",
        status=NotificationStatus.pending,
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    attempt_id = attempt.id
    session.close()

    mocker.patch("app.tasks.send_notification", side_effect=RuntimeError("boom"))

    try:
        process_notification(attempt_id, EVENT)
        assert False, "expected RuntimeError to propagate"
    except RuntimeError:
        pass

    check_session = session_factory()
    result = check_session.get(NotificationAttempt, attempt_id)
    assert result.status == NotificationStatus.failed
    assert result.retry_count == 1
    assert "boom" in result.error_message
