"""publications — ephemeral web surfaces

Revision ID: 013_publications
Revises: 012_learner_intelligence_cleanup
Create Date: 2026-09-24
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "013_publications"
down_revision: Union[str, None] = "012_learner_intelligence_cleanup"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "publications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("work_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("works.id", ondelete="SET NULL"), nullable=True),
        sa.Column("token_hash", sa.String(128), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("status", sa.String(40), server_default="active", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleaned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("schema_version", sa.String(20), server_default="1.0"),
        sa.Column("renderer_version", sa.String(20), server_default="1.0"),
        sa.Column("semantic", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("storage_uri", sa.String(1000), nullable=True),
        sa.Column("content_type", sa.String(100), server_default="text/html; charset=utf-8"),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("parent_publication_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("publications.id", ondelete="SET NULL"), nullable=True),
        sa.Column("artifact_ids", postgresql.JSONB(), server_default="[]"),
        sa.Column("access_count", sa.Integer(), server_default="0"),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), server_default="{}"),
    )
    op.create_index("ix_publication_principal", "publications", ["principal_id"])
    op.create_index("ix_publication_token_hash", "publications", ["token_hash"], unique=True)
    op.create_index("ix_publication_lifecycle", "publications", ["status", "expires_at"])
    op.create_index("ix_publication_cleanup", "publications", ["status", "cleaned_at"])


def downgrade() -> None:
    op.drop_index("ix_publication_cleanup", table_name="publications")
    op.drop_index("ix_publication_lifecycle", table_name="publications")
    op.drop_index("ix_publication_token_hash", table_name="publications")
    op.drop_index("ix_publication_principal", table_name="publications")
    op.drop_table("publications")
