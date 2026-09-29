"""add policy sections and effective dates

Revision ID: 1cbda6e90565
Revises: cf890101a965
Create Date: 2026-09-29 06:19:23.718203

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1cbda6e90565"
down_revision: str | None = "cf890101a965"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("policies", sa.Column("effective_from", sa.Date(), nullable=True))
    op.add_column("policies", sa.Column("effective_to", sa.Date(), nullable=True))
    op.create_table(
        "policy_sections",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("policy_id", sa.String(length=36), nullable=False),
        sa.Column("section_ref", sa.String(length=120), nullable=False),
        sa.Column("heading", sa.String(length=240), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["policy_id"], ["policies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_policy_sections_policy_id"),
        "policy_sections",
        ["policy_id"],
        unique=False,
    )
    op.create_index(
        "ix_policy_section_ref_unique",
        "policy_sections",
        ["policy_id", "section_ref"],
        unique=True,
    )
    op.create_index(
        "ix_policy_section_position_unique",
        "policy_sections",
        ["policy_id", "position"],
        unique=True,
    )
    op.execute(
        sa.text(
            """
            INSERT INTO policy_sections (id, policy_id, section_ref, heading, text, position)
            SELECT id, id, 'document', name, content, 0
            FROM policies
            """
        )
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_policy_section_position_unique", table_name="policy_sections")
    op.drop_index("ix_policy_section_ref_unique", table_name="policy_sections")
    op.drop_index(op.f("ix_policy_sections_policy_id"), table_name="policy_sections")
    op.drop_table("policy_sections")
    op.drop_column("policies", "effective_to")
    op.drop_column("policies", "effective_from")
