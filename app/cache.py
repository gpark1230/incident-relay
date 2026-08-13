import json

from redis import Redis

from app.config import settings

CACHE_KEY_PREFIX = "incident_cache:"


def _key(incident_id: int) -> str:
    return f"{CACHE_KEY_PREFIX}{incident_id}"


def get_cached_incident(redis_client: Redis, incident_id: int) -> dict | None:
    raw = redis_client.get(_key(incident_id))
    if raw is None:
        return None
    return json.loads(raw)


def set_cached_incident(redis_client: Redis, incident_id: int, data: dict) -> None:
    redis_client.set(_key(incident_id), json.dumps(data), ex=settings.cache_ttl_seconds)


def invalidate_incident(redis_client: Redis, incident_id: int) -> None:
    redis_client.delete(_key(incident_id))
