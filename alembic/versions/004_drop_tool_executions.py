"""Drop tool_executions table (tool architecture retired).

Revision ID: 004_drop_tool_executions
Revises: 003_retire_educational
"""

from typing import Sequence, Union

from alembic import op

revision: str = "004_drop_tool_executions"
down_revision: Union[str, None] = "003_retire_educational"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('DROP TABLE IF EXISTS "tool_executions" CASCADE')


def downgrade() -> None:
    pass
