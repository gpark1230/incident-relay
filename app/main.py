from fastapi import Depends, FastAPI, Query
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

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
