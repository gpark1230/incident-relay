import httpx

from app.config import settings
from app.events import IncidentEvent

EVENT_LABELS = {
    "incident.created": "New incident",
    "incident.updated": "Incident updated",
    "incident.commented": "New comment on incident",
}


def build_message(event: IncidentEvent, recipient: str) -> str:
    label = EVENT_LABELS.get(event["event"], event["event"])
    return (
        f"*{label}* — incident #{event['incident_id']} "
        f"(for {recipient})\n{event['details']}"
    )


def send_notification(event: IncidentEvent, recipient: str) -> None:
    """POST a message to the configured Slack/Discord incoming webhook.

    Raises on failure (non-2xx or network error) so callers — the RQ job —
    can catch it and let RQ's built-in Retry reschedule with backoff.
    """
    payload = {"text": build_message(event, recipient)}
    response = httpx.post(settings.notify_webhook_url, json=payload, timeout=5.0)
    response.raise_for_status()
