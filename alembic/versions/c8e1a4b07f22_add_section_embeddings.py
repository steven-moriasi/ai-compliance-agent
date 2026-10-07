"""add section embeddings

Revision ID: c8e1a4b07f22
Revises: b6d4e2a18c55
Create Date: 2026-10-07 14:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "c8e1a4b07f22"
down_revision: str | None = "b6d4e2a18c55"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Store 384-d section vectors on PostgreSQL. SQLite tests keep an in-memory index."""
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE TABLE section_embeddings (
            section_id VARCHAR(36) NOT NULL
                REFERENCES policy_sections (id) ON DELETE CASCADE,
            model_id VARCHAR(200) NOT NULL,
            content_sha256 CHAR(64) NOT NULL,
            embedding vector(384) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (section_id, model_id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_section_embeddings_hnsw
        ON section_embeddings
        USING hnsw (embedding vector_cosine_ops)
        """
    )


def downgrade() -> None:
    """Drop the vector table. SQLite never created it."""
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS ix_section_embeddings_hnsw")
    op.execute("DROP TABLE IF EXISTS section_embeddings")
