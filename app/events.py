from typing import TypedDict

# Must match IncidentDesk's app/events.py — the two services agree on this
# Redis list name and JSON shape as their only integration contract.
INCIDENT_EVENTS_KEY = "incident_events"


class IncidentEvent(TypedDict):
    event: str  # "incident.created" | "incident.updated" | "incident.commented"
    incident_id: int
    user_id: int
    details: str
    timestamp: str
