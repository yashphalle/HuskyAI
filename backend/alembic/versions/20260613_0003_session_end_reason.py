"""Add user_challenge_sessions.end_reason (manual vs timer_expired).

Revision ID: 20260613_0003
Revises: 20260405_0002
Create Date: 2026-06-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260613_0003"
down_revision: Union[str, None] = "20260405_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable: null for in-progress sessions and for sessions completed before
    # this field existed (end reason for those is unknown, not assumed).
    op.add_column(
        "user_challenge_sessions",
        sa.Column("end_reason", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_challenge_sessions", "end_reason")
