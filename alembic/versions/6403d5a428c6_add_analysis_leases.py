"""add analysis leases

Revision ID: 6403d5a428c6
Revises: 14429abb6a64
Create Date: 2026-09-10 01:06:07.243516

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6403d5a428c6"
down_revision: str | None = "14429abb6a64"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "compliance_cases",
        sa.Column("worker_id", sa.String(length=160), nullable=True),
    )
    op.add_column(
        "compliance_cases",
        sa.Column("fencing_token", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "compliance_cases",
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "compliance_cases",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.alter_column("compliance_cases", "fencing_token", server_default=None)
    op.alter_column("compliance_cases", "attempts", server_default=None)
    op.create_index(
        "ix_review_records_case_unique",
        "review_records",
        ["case_id"],
        unique=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_review_records_case_unique", table_name="review_records")
    op.drop_column("compliance_cases", "lease_expires_at")
    op.drop_column("compliance_cases", "attempts")
    op.drop_column("compliance_cases", "fencing_token")
    op.drop_column("compliance_cases", "worker_id")
