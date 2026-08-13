import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Response
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import cache
from app.config import settings
from app.db import get_db, get_redis
from app.models import NotificationAttempt, NotificationStatus
from app.schemas import NotificationAttemptOut

app = FastAPI(title="IncidentRelay")


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
        upstream = httpx.get(
            f"{settings.incident_desk_api_url}/incidents/{incident_id}", timeout=5.0
        )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"IncidentDesk unreachable: {exc}")

    if upstream.status_code == 404:
        raise HTTPException(status_code=404, detail="Incident not found")
    upstream.raise_for_status()

    data = upstream.json()
    cache.set_cached_incident(redis_client, incident_id, data)
    response.headers["X-Cache"] = "MISS"
    return data
