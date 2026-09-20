"""Channel link OTP challenges.

Revision ID: 009
Revises: 008
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels = None
depends_on = None


def _tables():
    return set(inspect(op.get_bind()).get_table_names(schema="public"))


def upgrade() -> None:
    if "channel_link_challenges" in _tables():
        print("alembic_009_skip_exists")
        return
    op.create_table(
        "channel_link_challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_channel", sa.String(40), nullable=False),
        sa.Column("target_channel", sa.String(40), nullable=False),
        sa.Column("target_external_id", sa.String(255), nullable=False),
        sa.Column("method", sa.String(40), server_default="otp", nullable=False),
        sa.Column("code_hash", sa.String(128), nullable=False),
        sa.Column("status", sa.String(40), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="5", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), server_default="{}", nullable=False),
    )
    op.create_index("ix_link_challenge_principal", "channel_link_challenges", ["principal_id"])
    op.create_index("ix_link_challenge_status", "channel_link_challenges", ["status", "expires_at"])
    print("alembic_009_ok")


def downgrade() -> None:
    op.drop_table("channel_link_challenges")
