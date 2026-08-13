from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app
from app.models import NotificationAttempt, NotificationStatus


def test_list_notifications_filters_by_incident_id(db_session):
    db_session.add_all(
        [
            NotificationAttempt(
                event="incident.created",
                incident_id=1,
                recipient="user:1",
                status=NotificationStatus.sent,
            ),
            NotificationAttempt(
                event="incident.created",
                incident_id=2,
                recipient="user:2",
                status=NotificationStatus.failed,
            ),
        ]
    )
    db_session.commit()

    app.dependency_overrides[get_db] = lambda: db_session
    client = TestClient(app)

    response = client.get("/notifications", params={"incident_id": 1})
    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert results[0]["incident_id"] == 1

    app.dependency_overrides.clear()
