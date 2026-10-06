"""add federal register source metadata

Revision ID: c4e8a1b27f30
Revises: 1cbda6e90565
Create Date: 2026-10-07 00:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c4e8a1b27f30"
down_revision: str | None = "1cbda6e90565"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("policies", sa.Column("content_sha256", sa.String(length=64), nullable=True))
    op.add_column("policies", sa.Column("document_number", sa.String(length=40), nullable=True))
    op.add_column("policies", sa.Column("title", sa.Text(), nullable=True))
    op.add_column("policies", sa.Column("source", sa.String(length=40), nullable=True))
    op.add_column("policies", sa.Column("source_url", sa.Text(), nullable=True))
    op.add_column("policies", sa.Column("document_type", sa.String(length=40), nullable=True))
    op.add_column("policies", sa.Column("publication_date", sa.Date(), nullable=True))
    op.add_column("policies", sa.Column("citation", sa.String(length=80), nullable=True))
    op.add_column("policies", sa.Column("cfr_references", sa.JSON(), nullable=True))
    op.add_column("policies", sa.Column("docket_ids", sa.JSON(), nullable=True))
    op.add_column(
        "policies",
        sa.Column("effective_date_source", sa.String(length=32), nullable=True),
    )
    op.add_column("policies", sa.Column("dataset_version", sa.String(length=64), nullable=True))
    op.add_column("policies", sa.Column("correction_of", sa.String(length=40), nullable=True))
    op.create_index("ix_policy_document_number", "policies", ["document_number"], unique=False)
    op.create_table(
        "ingestion_runs",
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("counts", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("running", "succeeded", "failed", native_enum=False),
            nullable=False,
        ),
        sa.Column("dataset_version", sa.String(length=64), nullable=True),
        sa.Column("error", sa.String(length=240), nullable=True),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index("ix_ingestion_runs_status", "ingestion_runs", ["status"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_ingestion_runs_status", table_name="ingestion_runs")
    op.drop_table("ingestion_runs")
    op.drop_index("ix_policy_document_number", table_name="policies")
    op.drop_column("policies", "correction_of")
    op.drop_column("policies", "dataset_version")
    op.drop_column("policies", "effective_date_source")
    op.drop_column("policies", "docket_ids")
    op.drop_column("policies", "cfr_references")
    op.drop_column("policies", "citation")
    op.drop_column("policies", "publication_date")
    op.drop_column("policies", "document_type")
    op.drop_column("policies", "source_url")
    op.drop_column("policies", "source")
    op.drop_column("policies", "title")
    op.drop_column("policies", "document_number")
    op.drop_column("policies", "content_sha256")
