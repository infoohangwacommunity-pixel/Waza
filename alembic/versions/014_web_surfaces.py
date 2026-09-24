"""web surfaces — AI-authored interactive environments

Revision ID: 014_web_surfaces
Revises: 013_publications
Create Date: 2026-09-24
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "014_web_surfaces"
down_revision: Union[str, None] = "013_publications"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "surfaces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("work_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("works.id", ondelete="SET NULL"), nullable=True),
        sa.Column("token_hash", sa.String(128), nullable=False),
        sa.Column("title", sa.String(500), server_default="WAX Surface", nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(40), server_default="creating", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleaned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_revision", sa.Integer(), server_default="0"),
        sa.Column("granted_scopes", postgresql.JSONB(), server_default="[]"),
        sa.Column("idempotency_key", sa.String(200), nullable=True),
        sa.Column("parent_surface_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("surfaces.id", ondelete="SET NULL"), nullable=True),
        sa.Column("access_count", sa.Integer(), server_default="0"),
        sa.Column("lifecycle_intent", sa.String(80), nullable=True),
        sa.Column("state_json", postgresql.JSONB(), server_default="{}"),
        sa.Column("metadata", postgresql.JSONB(), server_default="{}"),
    )
    op.create_index("ix_surface_principal", "surfaces", ["principal_id"])
    op.create_index("ix_surface_token_hash", "surfaces", ["token_hash"], unique=True)
    op.create_index("ix_surface_lifecycle", "surfaces", ["status", "expires_at"])
    op.create_index("ix_surface_activity", "surfaces", ["status", "last_activity_at"])
    op.create_index("ix_surface_idempotency", "surfaces", ["principal_id", "idempotency_key"], unique=True)

    op.create_table(
        "surface_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("surface_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("surfaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("entry_uri", sa.String(1000), nullable=True),
        sa.Column("content_type", sa.String(100), server_default="text/html; charset=utf-8"),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("checksum", sa.String(128), nullable=True),
        sa.Column("manifest", postgresql.JSONB(), server_default="{}"),
        sa.Column("source_note", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), server_default="{}"),
        sa.UniqueConstraint("surface_id", "revision", name="uq_surface_revision"),
    )
    op.create_index("ix_surface_revision_surface", "surface_revisions", ["surface_id"])

    op.create_table(
        "surface_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("surface_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("surfaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_token_hash", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", postgresql.JSONB(), server_default="[]"),
        sa.Column("metadata", postgresql.JSONB(), server_default="{}"),
    )
    op.create_index("ix_surface_session_surface", "surface_sessions", ["surface_id"])
    op.create_index("ix_surface_session_token", "surface_sessions", ["session_token_hash"], unique=True)
    op.create_index("ix_surface_session_expires", "surface_sessions", ["expires_at"])

    op.create_table(
        "surface_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("surface_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("surfaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("surface_sessions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("payload", postgresql.JSONB(), server_default="{}"),
        sa.Column("revision", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(40), server_default="browser"),
    )
    op.create_index("ix_surface_event_surface", "surface_events", ["surface_id", "created_at"])
    op.create_index("ix_surface_event_type", "surface_events", ["event_type"])


def downgrade() -> None:
    op.drop_table("surface_events")
    op.drop_table("surface_sessions")
    op.drop_table("surface_revisions")
    op.drop_table("surfaces")
