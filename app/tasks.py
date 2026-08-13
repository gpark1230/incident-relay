from app.db import SessionLocal
from app.events import IncidentEvent
from app.models import NotificationAttempt, NotificationStatus
from app.notifier import send_notification


def process_notification(attempt_id: int, event: IncidentEvent) -> None:
    """RQ job: send the notification for a previously-recorded attempt row.

    On failure this re-raises so RQ's Retry (configured at enqueue time)
    reschedules the same job — including the same attempt_id — which is
    what lets retry_count track attempts against one durable row instead
    of creating a new row per retry.
    """
    db = SessionLocal()
    try:
        attempt = db.get(NotificationAttempt, attempt_id)
        if attempt is None:
            return

        try:
            send_notification(event, attempt.recipient)
        except Exception as exc:
            attempt.status = NotificationStatus.failed
            attempt.retry_count += 1
            attempt.error_message = str(exc)[:500]
            db.commit()
            raise
        else:
            attempt.status = NotificationStatus.sent
            attempt.error_message = None
            db.commit()
    finally:
        db.close()
