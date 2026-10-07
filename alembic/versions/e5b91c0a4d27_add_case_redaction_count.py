"""add case redaction count

Revision ID: e5b91c0a4d27
Revises: c4e8a1b27f30
Create Date: 2026-10-07 10:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5b91c0a4d27"
down_revision: str | None = "c4e8a1b27f30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("compliance_cases", sa.Column("redaction_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("compliance_cases", "redaction_count")
