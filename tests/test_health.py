from fastapi.testclient import TestClient

from app.db import get_db, get_redis
from app.main import app


def test_health_ok(db_session, fake_redis):
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_redis] = lambda: fake_redis

    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["redis"] == "ok"

    app.dependency_overrides.clear()


def test_health_degraded_when_redis_down(db_session):
    class BrokenRedis:
        def ping(self):
            raise ConnectionError("redis unreachable")

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_redis] = lambda: BrokenRedis()

    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["redis"].startswith("error:")

    app.dependency_overrides.clear()
