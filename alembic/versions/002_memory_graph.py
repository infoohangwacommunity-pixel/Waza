"""Multi-layer episodic memory graph.

Revision ID: 002_memory_graph
Revises: 001_waza_baseline
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "002_memory_graph"
down_revision: Union[str, None] = "001_waza_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_episodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "principal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("principals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "work_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("works.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("channel", sa.String(length=40), nullable=True),
        sa.Column("importance", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="open"),
        sa.Column("structured", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_memory_episode_principal", "memory_episodes", ["principal_id"])
    op.create_index("ix_memory_episode_status", "memory_episodes", ["principal_id", "status"])
    op.create_index("ix_memory_episode_active", "memory_episodes", ["principal_id", "last_active_at"])

    op.create_table(
        "memory_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "principal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("principals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "from_memory_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memories.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "to_memory_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memories.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "episode_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memory_episodes.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("relation", sa.String(length=60), nullable=False),
        sa.Column("strength", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("structured", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_memory_link_principal", "memory_links", ["principal_id"])
    op.create_index("ix_memory_link_from", "memory_links", ["from_memory_id"])
    op.create_index("ix_memory_link_to", "memory_links", ["to_memory_id"])
    op.create_index("ix_memory_link_episode", "memory_links", ["episode_id"])
    op.create_index("ix_memory_link_relation", "memory_links", ["principal_id", "relation"])

    # Layer + episode membership on existing memories
    op.execute(
        "ALTER TABLE memories ADD COLUMN IF NOT EXISTS layer VARCHAR(40) DEFAULT 'semantic'"
    )
    op.execute(
        "ALTER TABLE memories ADD COLUMN IF NOT EXISTS episode_id UUID NULL "
        "REFERENCES memory_episodes(id) ON DELETE SET NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memory_layer ON memories (principal_id, layer)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memory_episode_id ON memories (episode_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_memory_episode_id")
    op.execute("DROP INDEX IF EXISTS ix_memory_layer")
    op.execute("ALTER TABLE memories DROP COLUMN IF EXISTS episode_id")
    op.execute("ALTER TABLE memories DROP COLUMN IF EXISTS layer")
    op.drop_table("memory_links")
    op.drop_table("memory_episodes")
