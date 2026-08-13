import httpx
from fastapi.testclient import TestClient

from app import cache
from app.db import get_redis
from app.main import app


def _mock_token(mocker, token="dummy-token"):
    return mocker.patch("app.main.incident_desk_auth.get_token", return_value=token)


def test_get_incident_cache_miss_then_hit(fake_redis, mocker):
    app.dependency_overrides[get_redis] = lambda: fake_redis
    _mock_token(mocker)

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
    assert mock_get.call_args.kwargs["headers"] == {"Authorization": "Bearer dummy-token"}

    second = client.get("/incidents/5")
    assert second.status_code == 200
    assert second.headers["X-Cache"] == "HIT"
    mock_get.assert_called_once()  # still only called once — second was a cache hit

    app.dependency_overrides.clear()


def test_get_incident_404_passthrough(fake_redis, mocker):
    app.dependency_overrides[get_redis] = lambda: fake_redis
    _mock_token(mocker)
    mocker.patch(
        "app.main.httpx.get",
        return_value=httpx.Response(404, request=httpx.Request("GET", "https://example.com")),
    )

    client = TestClient(app)
    response = client.get("/incidents/999")
    assert response.status_code == 404

    app.dependency_overrides.clear()


def test_get_incident_401_retries_once_then_passes_through(fake_redis, mocker):
    app.dependency_overrides[get_redis] = lambda: fake_redis
    mock_get_token = _mock_token(mocker)
    mock_invalidate = mocker.patch("app.main.incident_desk_auth.invalidate_token")
    mock_get = mocker.patch(
        "app.main.httpx.get",
        return_value=httpx.Response(
            401,
            json={"detail": "Not authenticated"},
            request=httpx.Request("GET", "https://example.com"),
        ),
    )

    client = TestClient(app)
    response = client.get("/incidents/5")

    assert response.status_code == 401
    assert response.json()["detail"] == {"detail": "Not authenticated"}
    # Retried exactly once: two upstream calls, one token invalidation.
    assert mock_get.call_count == 2
    assert mock_get_token.call_count == 2
    mock_invalidate.assert_called_once()

    app.dependency_overrides.clear()


def test_get_incident_401_then_success_on_retry(fake_redis, mocker):
    app.dependency_overrides[get_redis] = lambda: fake_redis
    _mock_token(mocker)
    mocker.patch("app.main.incident_desk_auth.invalidate_token")
    mocker.patch(
        "app.main.httpx.get",
        side_effect=[
            httpx.Response(401, request=httpx.Request("GET", "https://example.com")),
            httpx.Response(
                200,
                json={"id": 5, "title": "recovered"},
                request=httpx.Request("GET", "https://example.com"),
            ),
        ],
    )

    client = TestClient(app)
    response = client.get("/incidents/5")

    assert response.status_code == 200
    assert response.json() == {"id": 5, "title": "recovered"}

    app.dependency_overrides.clear()


def test_get_incident_does_not_cache_error_response(fake_redis, mocker):
    app.dependency_overrides[get_redis] = lambda: fake_redis
    _mock_token(mocker)
    mocker.patch("app.main.incident_desk_auth.invalidate_token")
    mocker.patch(
        "app.main.httpx.get",
        return_value=httpx.Response(401, request=httpx.Request("GET", "https://example.com")),
    )

    client = TestClient(app)
    client.get("/incidents/5")
    assert cache.get_cached_incident(fake_redis, 5) is None

    app.dependency_overrides.clear()


def test_get_incident_upstream_unreachable_returns_502(fake_redis, mocker):
    app.dependency_overrides[get_redis] = lambda: fake_redis
    _mock_token(mocker)
    mocker.patch(
        "app.main.httpx.get", side_effect=httpx.ConnectError("connection refused")
    )

    client = TestClient(app)
    response = client.get("/incidents/5")
    assert response.status_code == 502

    app.dependency_overrides.clear()


def test_get_incident_login_failure_returns_502(fake_redis, mocker):
    app.dependency_overrides[get_redis] = lambda: fake_redis
    mocker.patch(
        "app.main.incident_desk_auth.get_token",
        side_effect=httpx.HTTPStatusError(
            "401",
            request=httpx.Request("POST", "https://example.com/auth/login"),
            response=httpx.Response(401, request=httpx.Request("POST", "https://example.com/auth/login")),
        ),
    )

    client = TestClient(app)
    response = client.get("/incidents/5")
    assert response.status_code == 502
    assert "authentication failed" in response.json()["detail"]

    app.dependency_overrides.clear()
