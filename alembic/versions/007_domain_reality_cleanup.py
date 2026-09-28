"""Domain model cleanup: durable reality only.

- Drop check-in accounting columns on principal_workloads
- Drop memories.evidence (generic notes belong in structured/content)
- Rename surfaces.last_learner_interaction_at → last_user_interaction_at if present

Goals are Memory rows (memory_type), not a Goal table.

Revision ID: 007
Revises: 006
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    tables = set(insp.get_table_names())

    if "principal_workloads" in tables:
        cols = {c["name"] for c in insp.get_columns("principal_workloads")}
        for col in ("last_checkin_at", "checkin_week_start", "checkin_week_count"):
            if col in cols:
                try:
                    op.drop_column("principal_workloads", col)
                except Exception:
                    pass

    if "memories" in tables:
        cols = {c["name"] for c in insp.get_columns("memories")}
        if "evidence" in cols:
            try:
                op.drop_column("memories", "evidence")
            except Exception:
                pass

    if "surfaces" in tables:
        cols = {c["name"] for c in insp.get_columns("surfaces")}
        if "last_learner_interaction_at" in cols and "last_user_interaction_at" not in cols:
            op.alter_column(
                "surfaces",
                "last_learner_interaction_at",
                new_column_name="last_user_interaction_at",
            )


def downgrade() -> None:
    pass
