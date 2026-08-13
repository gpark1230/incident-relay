import json
import logging

from redis import Redis
from rq import Queue, Retry

from app.config import settings
from app.db import SessionLocal
from app.events import INCIDENT_EVENTS_KEY, IncidentEvent
from app.models import NotificationAttempt, NotificationStatus
from app.tasks import process_notification

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("incident_relay.listener")

NOTIFIABLE_EVENTS = {"incident.created", "incident.updated", "incident.commented"}
RETRY_INTERVALS_SECONDS = [10, 30, 90]


def handle_event(event: IncidentEvent, queue: Queue) -> None:
    if event.get("event") not in NOTIFIABLE_EVENTS:
        logger.info("Skipping unrecognized event type: %s", event.get("event"))
        return

    recipient = f"user:{event['user_id']}"

    db = SessionLocal()
    try:
        attempt = NotificationAttempt(
            event=event["event"],
            incident_id=event["incident_id"],
            recipient=recipient,
            status=NotificationStatus.pending,
        )
        db.add(attempt)
        db.commit()
        db.refresh(attempt)
    finally:
        db.close()

    queue.enqueue(
        process_notification,
        attempt.id,
        event,
        retry=Retry(max=len(RETRY_INTERVALS_SECONDS), interval=RETRY_INTERVALS_SECONDS),
    )
    logger.info(
        "Queued notification attempt %s for incident %s -> %s",
        attempt.id,
        event["incident_id"],
        recipient,
    )


def run() -> None:
    redis_conn = Redis.from_url(settings.redis_url)
    queue = Queue("notifications", connection=redis_conn)

    logger.info("Listening on Redis list '%s'...", INCIDENT_EVENTS_KEY)
    while True:
        # BLPOP blocks until an item is available, so this loop is idle
        # (no polling/spinning) between IncidentDesk events.
        _, raw = redis_conn.blpop(INCIDENT_EVENTS_KEY)
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Discarding malformed event payload: %r", raw)
            continue

        try:
            handle_event(event, queue)
        except Exception:
            logger.exception("Failed to handle event: %r", event)


if __name__ == "__main__":
    run()
