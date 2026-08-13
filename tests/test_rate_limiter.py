from app import rate_limiter


def test_allows_up_to_capacity_then_blocks(fake_redis, mocker):
    mocker.patch("app.rate_limiter.settings.rate_limit_max_tokens", 3)
    mocker.patch("app.rate_limiter.settings.rate_limit_window_seconds", 60)

    now = 1_000_000.0
    results = [rate_limiter.allow(fake_redis, "user:1", now) for _ in range(4)]

    assert results == [True, True, True, False]


def test_refills_over_time(fake_redis, mocker):
    mocker.patch("app.rate_limiter.settings.rate_limit_max_tokens", 2)
    mocker.patch("app.rate_limiter.settings.rate_limit_window_seconds", 10)

    now = 2_000_000.0
    assert rate_limiter.allow(fake_redis, "user:2", now) is True
    assert rate_limiter.allow(fake_redis, "user:2", now) is True
    assert rate_limiter.allow(fake_redis, "user:2", now) is False

    # Half the window later, one token (capacity/window * elapsed = 2/10*5 = 1) refilled
    assert rate_limiter.allow(fake_redis, "user:2", now + 5) is True
    assert rate_limiter.allow(fake_redis, "user:2", now + 5) is False


def test_separate_recipients_have_independent_buckets(fake_redis, mocker):
    mocker.patch("app.rate_limiter.settings.rate_limit_max_tokens", 1)
    mocker.patch("app.rate_limiter.settings.rate_limit_window_seconds", 60)

    now = 3_000_000.0
    assert rate_limiter.allow(fake_redis, "user:a", now) is True
    assert rate_limiter.allow(fake_redis, "user:a", now) is False
    assert rate_limiter.allow(fake_redis, "user:b", now) is True
