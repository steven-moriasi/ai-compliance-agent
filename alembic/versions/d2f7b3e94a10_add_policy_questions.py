"""add policy questions

Revision ID: d2f7b3e94a10
Revises: c8e1a4b07f22
Create Date: 2026-10-07 20:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d2f7b3e94a10"
down_revision: str | None = "c8e1a4b07f22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Queue questions for the worker and keep the sources each answer was given."""
    op.create_table(
        "policy_questions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "QUEUED",
                "ANSWERING",
                "ANSWERED",
                "UNANSWERED",
                "FAILED",
                name="questionstatus",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("requested_by", sa.String(length=160), nullable=False),
        sa.Column("retrieval_mode", sa.String(length=16), nullable=True),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column("validation_errors", sa.JSON(), nullable=False),
        sa.Column("injection_signals", sa.JSON(), nullable=False),
        sa.Column("redaction_count", sa.Integer(), nullable=True),
        sa.Column("model_provider", sa.String(length=80), nullable=True),
        sa.Column("model_name", sa.String(length=120), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("worker_id", sa.String(length=160), nullable=True),
        sa.Column("fencing_token", sa.Integer(), server_default="0", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_policy_questions_status"), "policy_questions", ["status"], unique=False
    )


def downgrade() -> None:
    """Drop stored questions and answers."""
    op.drop_index(op.f("ix_policy_questions_status"), table_name="policy_questions")
    op.drop_table("policy_questions")
