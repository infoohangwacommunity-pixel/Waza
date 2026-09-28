"""WAX durable-reality schema — single baseline, reproducible from zero.

Matches wax/db/models.py exactly. No educational/intelligence archaeology.

Revision ID: 001_reality
Revises: None
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001_reality"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create full schema from SQLAlchemy models (empty database)."""
    from wax.db.base import Base
    import wax.db.models  # noqa: F401 — register metadata

    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    from wax.db.base import Base
    import wax.db.models  # noqa: F401

    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
