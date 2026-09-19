"""Assessment tables.

Revision ID: 004
Revises: 003
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assessments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("activity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("activities.id", ondelete="SET NULL"), nullable=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("objective", sa.Text(), nullable=True),
        sa.Column("status", sa.String(40), server_default="draft"),
        sa.Column("timed", sa.Boolean(), server_default="false"),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settings", postgresql.JSONB(), server_default="{}"),
        sa.Column("metadata", postgresql.JSONB(), server_default="{}"),
    )
    op.create_index("ix_assessment_principal", "assessments", ["principal_id"])
    op.create_index("ix_assessment_status", "assessments", ["status"])

    op.create_table(
        "assessment_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("assessment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ordinal", sa.Integer(), server_default="0"),
        sa.Column("item_type", sa.String(40), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("options", postgresql.JSONB(), server_default="[]"),
        sa.Column("answer_key", postgresql.JSONB(), server_default="{}"),
        sa.Column("points", sa.Float(), server_default="1.0"),
        sa.Column("structured", postgresql.JSONB(), server_default="{}"),
        sa.Column("metadata", postgresql.JSONB(), server_default="{}"),
    )
    op.create_index("ix_aitem_assessment", "assessment_items", ["assessment_id"])

    op.create_table(
        "assessment_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("assessment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(40), server_default="in_progress"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("max_score", sa.Float(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("structured", postgresql.JSONB(), server_default="{}"),
        sa.Column("metadata", postgresql.JSONB(), server_default="{}"),
    )
    op.create_index("ix_aattempt_assessment", "assessment_attempts", ["assessment_id"])
    op.create_index("ix_aattempt_principal", "assessment_attempts", ["principal_id"])

    op.create_table(
        "assessment_responses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("assessment_attempts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("assessment_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("response_text", sa.Text(), nullable=True),
        sa.Column("response_structured", postgresql.JSONB(), server_default="{}"),
        sa.Column("is_correct", sa.Boolean(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column("evidence", postgresql.JSONB(), server_default="[]"),
        sa.Column("metadata", postgresql.JSONB(), server_default="{}"),
    )
    op.create_index("ix_aresp_attempt", "assessment_responses", ["attempt_id"])
    op.create_index("ix_aresp_item", "assessment_responses", ["item_id"])


def downgrade() -> None:
    op.drop_table("assessment_responses")
    op.drop_table("assessment_attempts")
    op.drop_table("assessment_items")
    op.drop_table("assessments")
