import httpx
from fastapi.testclient import TestClient

from app import cache
from app.db import get_redis
from app.main import app


def test_get_incident_cache_miss_then_hit(fake_redis, mocker):
    app.dependency_overrides[get_redis] = lambda: fake_redis

    mock_get = mocker.patch(
        "app.main.httpx.get",
        return_value=httpx.Response(
            200,
            json={"id": 5, "title": "db is down"},
            request=httpx.Request("GET", "https://example.com"),
        ),
    )

    client = TestClient(app)

    first = client.get("/incidents/5")
    assert first.status_code == 200
    assert first.headers["X-Cache"] == "MISS"
    assert first.json() == {"id": 5, "title": "db is down"}
    mock_get.assert_called_once()

    second = client.get("/incidents/5")
    assert second.status_code == 200
    assert second.headers["X-Cache"] == "HIT"
    mock_get.assert_called_once()  # still only called once — second was a cache hit

    app.dependency_overrides.clear()


def test_get_incident_404_passthrough(fake_redis, mocker):
    app.dependency_overrides[get_redis] = lambda: fake_redis
    mocker.patch(
        "app.main.httpx.get",
        return_value=httpx.Response(404, request=httpx.Request("GET", "https://example.com")),
    )

    client = TestClient(app)
    response = client.get("/incidents/999")
    assert response.status_code == 404

    app.dependency_overrides.clear()


def test_get_incident_upstream_unreachable_returns_502(fake_redis, mocker):
    app.dependency_overrides[get_redis] = lambda: fake_redis
    mocker.patch(
        "app.main.httpx.get", side_effect=httpx.ConnectError("connection refused")
    )

    client = TestClient(app)
    response = client.get("/incidents/5")
    assert response.status_code == 502

    app.dependency_overrides.clear()
