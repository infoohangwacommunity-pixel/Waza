"""Evidence, Hypothesis, and related indexes.

Revision ID: 003
Revises: 002
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("evidence_type", sa.String(80), nullable=False),
        sa.Column("claim_key", sa.String(500), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), server_default="{}"),
        sa.Column("assistance_level", sa.String(40), server_default="unknown"),
        sa.Column("weight", sa.Float(), server_default="0.5"),
        sa.Column("directness", sa.Float(), server_default="0.5"),
        sa.Column("independence", sa.Float(), server_default="0.5"),
        sa.Column("specificity", sa.Float(), server_default="0.5"),
        sa.Column("source", sa.String(50), server_default="observed"),
        sa.Column("observation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("observations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("work_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("works.id", ondelete="SET NULL"), nullable=True),
        sa.Column("assessment_response_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("assessment_responses.id", ondelete="SET NULL"), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), server_default="{}"),
    )
    op.create_index("ix_evidence_principal", "evidence", ["principal_id"])
    op.create_index("ix_evidence_kind", "evidence", ["evidence_type"])
    op.create_index("ix_evidence_claim", "evidence", ["claim_key"])

    op.create_table(
        "hypotheses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("claim_key", sa.String(500), nullable=False),
        sa.Column("claim", sa.Text(), nullable=False),
        sa.Column("status", sa.String(40), server_default="candidate"),
        sa.Column("confidence", sa.Float(), server_default="0.3"),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("supporting_evidence_ids", postgresql.JSONB(), server_default="[]"),
        sa.Column("contradicting_evidence_ids", postgresql.JSONB(), server_default="[]"),
        sa.Column("first_formed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hypotheses.id", ondelete="SET NULL"), nullable=True),
        sa.Column("memory_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("memories.id", ondelete="SET NULL"), nullable=True),
        sa.Column("structured", postgresql.JSONB(), server_default="{}"),
        sa.Column("metadata", postgresql.JSONB(), server_default="{}"),
    )
    op.create_index("ix_hyp_principal", "hypotheses", ["principal_id"])
    op.create_index("ix_hyp_status", "hypotheses", ["status"])
    op.create_index("ix_hyp_claim", "hypotheses", ["claim_key"])


def downgrade() -> None:
    op.drop_table("hypotheses")
    op.drop_table("evidence")
