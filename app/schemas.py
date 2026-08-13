from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models import NotificationStatus


class NotificationAttemptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event: str
    incident_id: int
    recipient: str
    status: NotificationStatus
    retry_count: int
    error_message: str | None
    created_at: datetime
    updated_at: datetime
