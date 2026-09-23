"""Cleanup retention columns deterministically (IF NOT EXISTS only).

Revision ID: 012_learner_intelligence_cleanup
Revises: 011_learner_intelligence
"""
from typing import Sequence, Union
from alembic import op

revision: str = "012_learner_intelligence_cleanup"
down_revision: Union[str, None] = "011_learner_intelligence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Deterministic, idempotent, no broad try/except
    op.execute("ALTER TABLE learner_concept_states ADD COLUMN IF NOT EXISTS stability DOUBLE PRECISION DEFAULT 1.0")
    op.execute("ALTER TABLE learner_concept_states ADD COLUMN IF NOT EXISTS difficulty DOUBLE PRECISION DEFAULT 0.3")
    op.execute("ALTER TABLE learner_concept_states ADD COLUMN IF NOT EXISTS retrievability DOUBLE PRECISION")
    op.execute("ALTER TABLE learner_concept_states ADD COLUMN IF NOT EXISTS lapse_count INTEGER DEFAULT 0")
    op.execute("ALTER TABLE learner_concept_states ADD COLUMN IF NOT EXISTS last_review_at TIMESTAMPTZ")
    op.execute("ALTER TABLE learner_concept_states ADD COLUMN IF NOT EXISTS next_review_at TIMESTAMPTZ")
    op.execute("ALTER TABLE learner_concept_states ADD COLUMN IF NOT EXISTS independent_successes INTEGER DEFAULT 0")
    op.execute("ALTER TABLE learner_concept_states ADD COLUMN IF NOT EXISTS assisted_successes INTEGER DEFAULT 0")
    op.execute("ALTER TABLE learner_concept_states ADD COLUMN IF NOT EXISTS review_count INTEGER DEFAULT 0")


def downgrade() -> None:
    pass  # retain columns
