"""Ensure full schema exists (checkfirst).

Replaces reliance on 001/002 create_all-only behavior for environments
that already stamped those revisions. Safe to run repeatedly.

Revision ID: 005
Revises: 004
"""
from typing import Sequence, Union

from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from wax.db.base import Base
    import wax.db.models  # noqa: F401

    bind = op.get_bind()
    # checkfirst=True: only create missing tables/indexes — does not drop columns
    Base.metadata.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    # Non-destructive ensure migration — no automatic drop
    pass
