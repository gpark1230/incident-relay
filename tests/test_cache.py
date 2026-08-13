from app import cache


def test_set_then_get_returns_cached_value(fake_redis):
    cache.set_cached_incident(fake_redis, 5, {"id": 5, "title": "db is down"})
    result = cache.get_cached_incident(fake_redis, 5)
    assert result == {"id": 5, "title": "db is down"}


def test_get_returns_none_when_not_cached(fake_redis):
    assert cache.get_cached_incident(fake_redis, 999) is None


def test_invalidate_removes_cached_value(fake_redis):
    cache.set_cached_incident(fake_redis, 5, {"id": 5, "title": "db is down"})
    cache.invalidate_incident(fake_redis, 5)
    assert cache.get_cached_incident(fake_redis, 5) is None


def test_set_respects_ttl(fake_redis, mocker):
    mocker.patch("app.cache.settings.cache_ttl_seconds", 30)
    cache.set_cached_incident(fake_redis, 5, {"id": 5})
    ttl = fake_redis.ttl(cache._key(5))
    assert 0 < ttl <= 30
