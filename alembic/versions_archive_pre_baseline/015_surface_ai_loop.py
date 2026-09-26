"""surface activity, retention, AI request loop

Revision ID: 015_surface_ai_loop
Revises: 014_web_surfaces
Create Date: 2026-09-24
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "015_surface_ai_loop"
down_revision: Union[str, None] = "014_web_surfaces"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("surfaces", sa.Column("last_opened_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("surfaces", sa.Column("last_learner_interaction_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("surfaces", sa.Column("last_ai_request_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("surfaces", sa.Column("last_ai_response_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("surfaces", sa.Column("last_revision_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("surfaces", sa.Column("retention_requested", sa.Boolean(), server_default=sa.text("false")))
    op.add_column("surfaces", sa.Column("related_surface_ids", postgresql.JSONB(), server_default="[]"))

    op.create_table(
        "surface_ai_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("surface_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("surfaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_token_hash", sa.String(128), nullable=False),
        sa.Column("status", sa.String(40), server_default="accepted"),
        sa.Column("message_preview", sa.String(240), nullable=True),
        sa.Column("reply_preview", sa.Text(), nullable=True),
        sa.Column("work_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("works.id", ondelete="SET NULL"), nullable=True),
        sa.Column("idempotency_key", sa.String(200), nullable=True),
        sa.Column("error", sa.String(300), nullable=True),
        sa.Column("result", postgresql.JSONB(), server_default="{}"),
        sa.Column("metadata", postgresql.JSONB(), server_default="{}"),
    )
    op.create_index("ix_surface_ai_req_surface", "surface_ai_requests", ["surface_id", "created_at"])
    op.create_index("ix_surface_ai_req_status", "surface_ai_requests", ["status"])
    op.create_index("uq_surface_ai_request_token", "surface_ai_requests", ["request_token_hash"], unique=True)


def downgrade() -> None:
    op.drop_table("surface_ai_requests")
    op.drop_column("surfaces", "related_surface_ids")
    op.drop_column("surfaces", "retention_requested")
    op.drop_column("surfaces", "last_revision_at")
    op.drop_column("surfaces", "last_ai_response_at")
    op.drop_column("surfaces", "last_ai_request_at")
    op.drop_column("surfaces", "last_learner_interaction_at")
    op.drop_column("surfaces", "last_opened_at")
