"""Knowledge graph / activities baseline (noop if 001 already created them).

Revision ID: 002
Revises: 001
"""
from typing import Sequence, Union

from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from wax.db.base import Base
    import wax.db.models  # noqa: F401

    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    pass
