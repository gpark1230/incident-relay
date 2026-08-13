"""add rate_limited status

Revision ID: a1c2d3e4f5g6
Revises: 63b6ca6ffebc
Create Date: 2026-08-13

"""
from typing import Sequence, Union

from alembic import op

revision: str = "a1c2d3e4f5g6"
down_revision: Union[str, None] = "63b6ca6ffebc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE notificationstatus ADD VALUE IF NOT EXISTS 'rate_limited'")


def downgrade() -> None:
    # Postgres has no DROP VALUE for enums; downgrading would require
    # recreating the type. Left as a no-op since this is additive-only.
    pass
