"""Drop memory graph (episodes/links) and unused embedding/layer columns.

Memory is durable student state rows only — AI-owned lifecycle, not a graph engine.

Revision ID: 006
Revises: 005
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    tables = set(insp.get_table_names())

    if "memories" in tables:
        cols = {c["name"] for c in insp.get_columns("memories")}
        # Drop FK to episodes before dropping episodes
        if "episode_id" in cols:
            try:
                op.drop_constraint("memories_episode_id_fkey", "memories", type_="foreignkey")
            except Exception:
                pass
            try:
                op.drop_column("memories", "episode_id")
            except Exception:
                pass
        if "embedding" in cols:
            try:
                op.drop_column("memories", "embedding")
            except Exception:
                pass
        if "layer" in cols:
            try:
                op.drop_column("memories", "layer")
            except Exception:
                pass

    if "memory_links" in tables:
        op.drop_table("memory_links")
    if "memory_episodes" in tables:
        for ix in ("ix_memory_episode_principal", "ix_memory_episode_status", "ix_memory_episode_active"):
            try:
                op.drop_index(ix, table_name="memory_episodes")
            except Exception:
                pass
        op.drop_table("memory_episodes")


def downgrade() -> None:
    pass
