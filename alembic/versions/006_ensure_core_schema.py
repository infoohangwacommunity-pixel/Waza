"""Ensure core schema tables exist (idempotent).

Safe for production DBs that reached HEAD without tables (e.g. prior
failed/stamped migrations). Uses checkfirst=True only — never drops.

Revision ID: 006
Revises: 005
"""
from typing import Sequence, Union

from alembic import op

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from wax.db.base import Base
    import wax.db.models  # noqa: F401

    bind = op.get_bind()
    # checkfirst=True: create only missing tables/indexes/constraints
    Base.metadata.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    # Non-destructive ensure migration
    pass
