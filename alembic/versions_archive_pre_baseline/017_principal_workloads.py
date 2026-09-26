"""durable principal workload (rate + check-in accounting)

Revision ID: 017_principal_workloads
Revises: 016_surface_hardening
Create Date: 2026-09-25
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "017_principal_workloads"
down_revision: Union[str, None] = "016_surface_hardening"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "principal_workloads",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tokens", sa.Float(), nullable=False, server_default="12"),
        sa.Column("last_refill_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checkin_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checkin_week_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checkin_week_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.ForeignKeyConstraint(["principal_id"], ["principals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("principal_id", name="uq_principal_workload"),
    )
    op.create_index("ix_principal_workload_principal", "principal_workloads", ["principal_id"])


def downgrade() -> None:
    op.drop_index("ix_principal_workload_principal", table_name="principal_workloads")
    op.drop_table("principal_workloads")
