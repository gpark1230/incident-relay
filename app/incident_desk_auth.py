import httpx
from redis import Redis

from app.config import settings

TOKEN_CACHE_KEY = "incident_desk:auth_token"
# IncidentDesk issues tokens with a 60-minute expiry; cache for less than
# that so we always refresh before IncidentDesk itself would reject it.
TOKEN_CACHE_TTL_SECONDS = 55 * 60


def get_token(redis_client: Redis) -> str:
    cached = redis_client.get(TOKEN_CACHE_KEY)
    if cached is not None:
        return cached.decode() if isinstance(cached, bytes) else cached
    return _login_and_cache(redis_client)


def invalidate_token(redis_client: Redis) -> None:
    redis_client.delete(TOKEN_CACHE_KEY)


def _login_and_cache(redis_client: Redis) -> str:
    response = httpx.post(
        f"{settings.incident_desk_api_url}/auth/login",
        data={
            "username": settings.incident_desk_service_email,
            "password": settings.incident_desk_service_password,
        },
        timeout=5.0,
    )
    response.raise_for_status()
    token = response.json()["access_token"]
    redis_client.set(TOKEN_CACHE_KEY, token, ex=TOKEN_CACHE_TTL_SECONDS)
    return token
