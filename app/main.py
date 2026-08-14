from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import cache, incident_desk_auth
from app.config import settings
from app.db import get_db, get_redis
from app.models import NotificationAttempt, NotificationStatus
from app.schemas import NotificationAttemptOut

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="IncidentRelay")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health(db: Session = Depends(get_db), redis_client: Redis = Depends(get_redis)):
    checks = {"database": "unknown", "redis": "unknown"}

    try:
        db.execute(select(1))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc}"

    try:
        redis_client.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {exc}"

    status = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    return {"status": status, "checks": checks}


@app.get("/notifications", response_model=list[NotificationAttemptOut])
def list_notifications(
    incident_id: int | None = None,
    status: NotificationStatus | None = None,
    limit: int = Query(default=50, le=200),
    db: Session = Depends(get_db),
):
    stmt = select(NotificationAttempt).order_by(NotificationAttempt.created_at.desc())
    if incident_id is not None:
        stmt = stmt.where(NotificationAttempt.incident_id == incident_id)
    if status is not None:
        stmt = stmt.where(NotificationAttempt.status == status)
    stmt = stmt.limit(limit)
    return db.execute(stmt).scalars().all()


def _fetch_incident_from_upstream(
    incident_id: int, redis_client: Redis, retry_on_auth_failure: bool = True
) -> httpx.Response:
    token = incident_desk_auth.get_token(redis_client)
    upstream = httpx.get(
        f"{settings.incident_desk_api_url}/incidents/{incident_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=5.0,
    )
    if upstream.status_code == 401 and retry_on_auth_failure:
        # Cached token may have expired early (clock skew) or been
        # revoked -- get a fresh one and try exactly once more.
        incident_desk_auth.invalidate_token(redis_client)
        return _fetch_incident_from_upstream(
            incident_id, redis_client, retry_on_auth_failure=False
        )
    return upstream


@app.get("/incidents/{incident_id}")
def get_incident(
    incident_id: int, response: Response, redis_client: Redis = Depends(get_redis)
):
    """Cached read-through proxy for IncidentDesk's GET /incidents/{id}.

    Cached in Redis with a short TTL; invalidated by the listener when it
    processes an incident.updated event for this same incident_id.
    """
    cached = cache.get_cached_incident(redis_client, incident_id)
    if cached is not None:
        response.headers["X-Cache"] = "HIT"
        return cached

    try:
        upstream = _fetch_incident_from_upstream(incident_id, redis_client)
    except httpx.HTTPStatusError as exc:
        # Raised by the login call's raise_for_status() -- e.g. the
        # service account's credentials were rejected. This is a real
        # IncidentDesk-side auth problem, not this specific incident's
        # fault, so it isn't a 401 on THIS request -- surface it as 502.
        raise HTTPException(
            status_code=502, detail=f"IncidentDesk authentication failed: {exc}"
        )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"IncidentDesk unreachable: {exc}")

    if upstream.status_code >= 400:
        # Pass the real upstream status through (401 stays 401, 404 stays
        # 404, ...) instead of raise_for_status() turning every error into
        # an opaque 500 -- callers need to know *why* the lookup failed.
        try:
            detail = upstream.json()
        except ValueError:
            detail = upstream.text
        raise HTTPException(status_code=upstream.status_code, detail=detail)

    data = upstream.json()
    cache.set_cached_incident(redis_client, incident_id, data)
    response.headers["X-Cache"] = "MISS"
    return data
