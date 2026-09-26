"""Durable interactions for server-authoritative choices.

Revision ID: 008
Revises: 007
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels = None
depends_on = None


def _tables():
    return set(inspect(op.get_bind()).get_table_names(schema="public"))


def upgrade() -> None:
    if "interactions" in _tables():
        print("alembic_008_skip_exists")
        return
    op.create_table(
        "interactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("activity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("activities.id", ondelete="SET NULL"), nullable=True),
        sa.Column("work_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("works.id", ondelete="SET NULL"), nullable=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("channel", sa.String(40), nullable=False),
        sa.Column("callback_token", sa.String(64), nullable=False),
        sa.Column("external_message_id", sa.String(128), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("style", sa.String(40), server_default="buttons", nullable=False),
        sa.Column("choices", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("status", sa.String(40), server_default="pending", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_choice_id", sa.String(256), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.UniqueConstraint("callback_token", name="uq_interaction_callback_token"),
    )
    op.create_index("ix_interaction_principal", "interactions", ["principal_id"])
    op.create_index("ix_interaction_status_expires", "interactions", ["status", "expires_at"])
    print("alembic_008_ok")


def downgrade() -> None:
    op.drop_table("interactions")
