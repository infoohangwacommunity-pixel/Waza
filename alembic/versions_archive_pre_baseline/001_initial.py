"""Initial core schema (greenfield).

Uses metadata.create_all(checkfirst=True) so empty databases get the full
current model set. Subsequent revisions 003+ add explicit DDL for new tables
so already-stamped databases receive proper upgrades.

Revision ID: 001
Revises:
"""
from typing import Sequence, Union

from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    from wax.db.base import Base
    import wax.db.models  # noqa: F401

    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    # Destructive — only for empty/dev databases
    from wax.db.base import Base
    import wax.db.models  # noqa: F401

    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
