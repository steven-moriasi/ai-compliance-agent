"""add policy section search vector

Revision ID: f3a8c1d09e44
Revises: e5b91c0a4d27
Create Date: 2026-10-07 12:50:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "f3a8c1d09e44"
down_revision: str | None = "e5b91c0a4d27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add full-text search on PostgreSQL. SQLite keeps the Python keyword path."""
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        ALTER TABLE policy_sections
        ADD COLUMN search_vector tsvector
        GENERATED ALWAYS AS (
            to_tsvector('english', coalesce(heading, '') || ' ' || text)
        ) STORED
        """
    )
    op.execute(
        "CREATE INDEX ix_policy_sections_search ON policy_sections USING GIN (search_vector)"
    )
    op.create_index(
        "ix_policies_status_window",
        "policies",
        ["status", "effective_from", "effective_to"],
    )


def downgrade() -> None:
    """Remove the PostgreSQL full-text column. SQLite has nothing to undo."""
    if op.get_bind().dialect.name != "postgresql":
        return
    op.drop_index("ix_policies_status_window", table_name="policies")
    op.execute("DROP INDEX IF EXISTS ix_policy_sections_search")
    op.drop_column("policy_sections", "search_vector")
