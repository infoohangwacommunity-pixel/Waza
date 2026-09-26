"""worlds table — durable personal computing world identity + lifecycle

Revision ID: 010_worlds
Revises: 009_channel_link_challenges
Create Date: 2026-09-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "010_worlds"
down_revision: Union[str, None] = "009_channel_link_challenges"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "worlds",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lifecycle_state", sa.String(40), nullable=False, server_default="READY"),
        sa.Column("lifecycle_reason", sa.Text(), nullable=True),
        sa.Column("schema_version", sa.Integer(), server_default="1"),
        sa.Column("disk_used_bytes", sa.Integer(), nullable=True),
        sa.Column("env_size_bytes", sa.Integer(), nullable=True),
        sa.Column("root_path", sa.String(1000), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("principal_id", name="uq_world_principal"),
    )
    op.create_index("ix_world_lifecycle", "worlds", ["lifecycle_state"])


def downgrade() -> None:
    op.drop_index("ix_world_lifecycle", table_name="worlds")
    op.drop_table("worlds")
