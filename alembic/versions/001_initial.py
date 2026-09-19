"""Initial schema from models.

Revision ID: 001
Revises:
Create Date: 2026-09-19
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create tables from current metadata so schema always matches models.
    from wax.db.base import Base
    import wax.db.models  # noqa: F401
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    from wax.db.base import Base
    import wax.db.models  # noqa: F401
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
