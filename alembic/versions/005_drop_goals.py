"""Drop goals table — durable intent lives in AI-owned memory, not a Goal engine.

Revision ID: 005
Revises: 004
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop FKs from other tables if present (baseline had optional goal_id cols)
    conn = op.get_bind()
    insp = sa.inspect(conn)
    tables = insp.get_table_names()
    for table in tables:
        cols = {c["name"] for c in insp.get_columns(table)}
        if "goal_id" in cols:
            try:
                op.drop_constraint(f"{table}_goal_id_fkey", table, type_="foreignkey")
            except Exception:
                pass
            try:
                op.drop_column(table, "goal_id")
            except Exception:
                pass
    if "goals" in tables:
        op.drop_index("ix_goal_status", table_name="goals")
        op.drop_index("ix_goal_principal", table_name="goals")
        op.drop_table("goals")


def downgrade() -> None:
    pass
