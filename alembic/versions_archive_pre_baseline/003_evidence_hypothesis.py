"""Evidence, Hypothesis, and related indexes.

Idempotent: skips create if tables already exist (e.g. after 001 create_all).
Without this, upgrade head fails with DuplicateTableError on evidence and the
entire transactional migration chain rolls back — leaving an empty database.

Revision ID: 003
Revises: 002
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names(schema="public"))


def _has_index(table: str, name: str) -> bool:
    insp = inspect(op.get_bind())
    if table not in insp.get_table_names(schema="public"):
        return False
    return any(ix["name"] == name for ix in insp.get_indexes(table, schema="public"))


def upgrade() -> None:
    existing = _tables()

    if "evidence" not in existing:
        # Do not FK assessment_responses here — that table is created in 004.
        # Column remains nullable; FK can be added later if desired.
        op.create_table(
            "evidence",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True)),
            sa.Column("updated_at", sa.DateTime(timezone=True)),
            sa.Column(
                "principal_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("principals.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("evidence_type", sa.String(80), nullable=False),
            sa.Column("claim_key", sa.String(500), nullable=True),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("payload", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
            sa.Column("assistance_level", sa.String(40), server_default="unknown"),
            sa.Column("weight", sa.Float(), server_default="0.5"),
            sa.Column("directness", sa.Float(), server_default="0.5"),
            sa.Column("independence", sa.Float(), server_default="0.5"),
            sa.Column("specificity", sa.Float(), server_default="0.5"),
            sa.Column("source", sa.String(50), server_default="observed"),
            sa.Column(
                "observation_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("observations.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "message_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("messages.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "work_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("works.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("assessment_response_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        )
    if not _has_index("evidence", "ix_evidence_principal"):
        op.create_index("ix_evidence_principal", "evidence", ["principal_id"])
    if not _has_index("evidence", "ix_evidence_kind"):
        op.create_index("ix_evidence_kind", "evidence", ["evidence_type"])
    if not _has_index("evidence", "ix_evidence_claim"):
        op.create_index("ix_evidence_claim", "evidence", ["claim_key"])

    existing = _tables()
    if "hypotheses" not in existing:
        op.create_table(
            "hypotheses",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True)),
            sa.Column("updated_at", sa.DateTime(timezone=True)),
            sa.Column(
                "principal_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("principals.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("claim_key", sa.String(500), nullable=False),
            sa.Column("claim", sa.Text(), nullable=False),
            sa.Column("status", sa.String(40), server_default="candidate"),
            sa.Column("confidence", sa.Float(), server_default="0.3"),
            sa.Column("rationale", sa.Text(), nullable=True),
            sa.Column(
                "supporting_evidence_ids",
                postgresql.JSONB(),
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column(
                "contradicting_evidence_ids",
                postgresql.JSONB(),
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column("first_formed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "superseded_by_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("hypotheses.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "memory_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("memories.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("structured", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
            sa.Column("metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        )
    if not _has_index("hypotheses", "ix_hyp_principal"):
        op.create_index("ix_hyp_principal", "hypotheses", ["principal_id"])
    if not _has_index("hypotheses", "ix_hyp_status"):
        op.create_index("ix_hyp_status", "hypotheses", ["status"])
    if not _has_index("hypotheses", "ix_hyp_claim"):
        op.create_index("ix_hyp_claim", "hypotheses", ["claim_key"])


def downgrade() -> None:
    op.drop_table("hypotheses")
    op.drop_table("evidence")
