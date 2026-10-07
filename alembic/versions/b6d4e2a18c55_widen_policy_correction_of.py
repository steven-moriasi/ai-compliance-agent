"""widen policy correction_of

Revision ID: b6d4e2a18c55
Revises: f3a8c1d09e44
Create Date: 2026-10-07 13:05:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b6d4e2a18c55"
down_revision: str | None = "f3a8c1d09e44"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """SQLite ignores varchar length. PostgreSQL rejected a correction URL longer than 40."""
    with op.batch_alter_table("policies") as batch:
        batch.alter_column(
            "correction_of",
            existing_type=sa.String(length=40),
            type_=sa.Text(),
            existing_nullable=True,
        )


def downgrade() -> None:
    """Restore the original width. Values longer than 40 characters do not fit."""
    with op.batch_alter_table("policies") as batch:
        batch.alter_column(
            "correction_of",
            existing_type=sa.Text(),
            type_=sa.String(length=40),
            existing_nullable=True,
        )
