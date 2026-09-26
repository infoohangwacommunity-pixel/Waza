"""surface hardening — AI request uniqueness, state revision

Revision ID: 016_surface_hardening
Revises: 015_surface_ai_loop
Create Date: 2026-09-24
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "016_surface_hardening"
down_revision: Union[str, None] = "015_surface_ai_loop"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Partial unique index: only one AI request per (surface, idempotency_key) when key set
    op.create_index(
        "uq_surface_ai_req_idempotency",
        "surface_ai_requests",
        ["surface_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    # Optimistic concurrency for surface state blob
    op.add_column(
        "surfaces",
        sa.Column("state_revision", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("surfaces", "state_revision")
    op.drop_index("uq_surface_ai_req_idempotency", table_name="surface_ai_requests")
