import httpx

from app import incident_desk_auth


def test_get_token_returns_cached_value_without_logging_in(fake_redis, mocker):
    fake_redis.set(incident_desk_auth.TOKEN_CACHE_KEY, "cached-token")
    mock_post = mocker.patch("app.incident_desk_auth.httpx.post")

    token = incident_desk_auth.get_token(fake_redis)

    assert token == "cached-token"
    mock_post.assert_not_called()


def test_get_token_logs_in_and_caches_when_nothing_cached(fake_redis, mocker):
    mocker.patch("app.incident_desk_auth.settings.incident_desk_service_email", "svc@example.com")
    mocker.patch("app.incident_desk_auth.settings.incident_desk_service_password", "hunter2")
    mock_post = mocker.patch(
        "app.incident_desk_auth.httpx.post",
        return_value=httpx.Response(
            200,
            json={"access_token": "fresh-token", "token_type": "bearer"},
            request=httpx.Request("POST", "https://example.com/auth/login"),
        ),
    )

    token = incident_desk_auth.get_token(fake_redis)

    assert token == "fresh-token"
    mock_post.assert_called_once()
    assert mock_post.call_args.kwargs["data"] == {
        "username": "svc@example.com",
        "password": "hunter2",
    }
    assert fake_redis.get(incident_desk_auth.TOKEN_CACHE_KEY).decode() == "fresh-token"
    ttl = fake_redis.ttl(incident_desk_auth.TOKEN_CACHE_KEY)
    assert 0 < ttl <= incident_desk_auth.TOKEN_CACHE_TTL_SECONDS


def test_get_token_raises_on_login_failure(fake_redis, mocker):
    mocker.patch(
        "app.incident_desk_auth.httpx.post",
        return_value=httpx.Response(
            401, request=httpx.Request("POST", "https://example.com/auth/login")
        ),
    )

    try:
        incident_desk_auth.get_token(fake_redis)
        assert False, "expected HTTPStatusError"
    except httpx.HTTPStatusError:
        pass


def test_invalidate_token_removes_cached_value(fake_redis):
    fake_redis.set(incident_desk_auth.TOKEN_CACHE_KEY, "some-token")
    incident_desk_auth.invalidate_token(fake_redis)
    assert fake_redis.get(incident_desk_auth.TOKEN_CACHE_KEY) is None
