"""learner events, temporal intents, retention columns

Revision ID: 011_learner_intelligence
Revises: 010_worlds
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "011_learner_intelligence"
down_revision: Union[str, None] = "010_worlds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "learning_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(80), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), server_default="{}"),
        sa.Column("concept_key", sa.String(500), nullable=True),
        sa.Column("goal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("goals.id", ondelete="SET NULL"), nullable=True),
        sa.Column("activity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("activities.id", ondelete="SET NULL"), nullable=True),
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("evidence.id", ondelete="SET NULL"), nullable=True),
        sa.Column("memory_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("memories.id", ondelete="SET NULL"), nullable=True),
        sa.Column("scheduled_action_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("scheduled_actions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("work_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("works.id", ondelete="SET NULL"), nullable=True),
        sa.Column("confidence", sa.Float(), server_default="0.7"),
        sa.Column("observed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_learning_event_principal_time", "learning_events", ["principal_id", "observed_at"])
    op.create_index("ix_learning_event_kind", "learning_events", ["principal_id", "kind"])
    op.create_index("ix_learning_event_concept", "learning_events", ["principal_id", "concept_key"])

    op.create_table(
        "temporal_intents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("purpose", sa.String(80), nullable=False),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("execute_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(80), server_default="UTC"),
        sa.Column("status", sa.String(40), server_default="scheduled"),
        sa.Column("urgency", sa.String(40), server_default="normal"),
        sa.Column("flexibility", sa.String(40), server_default="hard"),
        sa.Column("completion_condition", sa.Text(), nullable=True),
        sa.Column("original_request", sa.Text(), nullable=True),
        sa.Column("original_context", postgresql.JSONB(), server_default="{}"),
        sa.Column("payload", postgresql.JSONB(), server_default="{}"),
        sa.Column("scheduled_action_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("scheduled_actions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("concept_key", sa.String(500), nullable=True),
        sa.Column("goal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("goals.id", ondelete="SET NULL"), nullable=True),
        sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evaluation", postgresql.JSONB(), server_default="{}"),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_temporal_intent_due", "temporal_intents", ["status", "execute_at"])
    op.create_index("ix_temporal_intent_principal", "temporal_intents", ["principal_id", "status"])

    # Retention / spaced-repetition columns on learner_concept_states.
    # Idempotent ADD COLUMN IF NOT EXISTS so partial prior applies and
    # re-runs are safe. No broad try/except — failures surface clearly.
    # 012_learner_intelligence_cleanup repeats these as a safety net.
    op.execute(
        "ALTER TABLE learner_concept_states "
        "ADD COLUMN IF NOT EXISTS stability DOUBLE PRECISION DEFAULT 1.0"
    )
    op.execute(
        "ALTER TABLE learner_concept_states "
        "ADD COLUMN IF NOT EXISTS difficulty DOUBLE PRECISION DEFAULT 0.3"
    )
    op.execute(
        "ALTER TABLE learner_concept_states "
        "ADD COLUMN IF NOT EXISTS retrievability DOUBLE PRECISION"
    )
    op.execute(
        "ALTER TABLE learner_concept_states "
        "ADD COLUMN IF NOT EXISTS lapse_count INTEGER DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE learner_concept_states "
        "ADD COLUMN IF NOT EXISTS last_review_at TIMESTAMPTZ"
    )
    op.execute(
        "ALTER TABLE learner_concept_states "
        "ADD COLUMN IF NOT EXISTS next_review_at TIMESTAMPTZ"
    )
    op.execute(
        "ALTER TABLE learner_concept_states "
        "ADD COLUMN IF NOT EXISTS independent_successes INTEGER DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE learner_concept_states "
        "ADD COLUMN IF NOT EXISTS assisted_successes INTEGER DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE learner_concept_states "
        "ADD COLUMN IF NOT EXISTS review_count INTEGER DEFAULT 0"
    )



def downgrade() -> None:
    op.drop_table("temporal_intents")
    op.drop_table("learning_events")
