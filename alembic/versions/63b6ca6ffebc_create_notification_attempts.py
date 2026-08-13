"""create notification_attempts

Revision ID: 63b6ca6ffebc
Revises:
Create Date: 2026-08-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "63b6ca6ffebc"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    notification_status = sa.Enum(
        "pending", "sent", "failed", name="notificationstatus"
    )

    op.create_table(
        "notification_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event", sa.String(), nullable=False),
        sa.Column("incident_id", sa.Integer(), nullable=False),
        sa.Column("recipient", sa.String(), nullable=False),
        sa.Column("status", notification_status, nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_notification_attempts_incident_id",
        "notification_attempts",
        ["incident_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_notification_attempts_incident_id", table_name="notification_attempts")
    op.drop_table("notification_attempts")
    sa.Enum(name="notificationstatus").drop(op.get_bind(), checkfirst=True)
