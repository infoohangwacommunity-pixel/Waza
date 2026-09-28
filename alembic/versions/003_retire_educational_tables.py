"""Retire educational/intelligence feature tables.

Revision ID: 003_retire_educational
Revises: 002_memory_graph

Drops tables that existed only for the previous architecture:
assessment, concept graph, evidence/hypothesis, knowledge chunks,
automatic observations, publications, etc.

Surfaces, memory lifecycle, World, Work, and scheduling remain.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "003_retire_educational"
down_revision: Union[str, None] = "002_memory_graph"
branch_labels = None
depends_on = None

TABLES = [
    "assessment_responses",
    "assessment_attempts",
    "assessment_items",
    "assessments",
    "hypotheses",
    "evidence",
    "learner_concept_states",
    "concept_relations",
    "concepts",
    "document_chunks",
    "knowledge_sources",
    "misconceptions",
    "observations",
    "activities",
    "learning_events",
    "learning_observations",
    "temporal_intents",
    "publications",
]


def upgrade() -> None:
    for t in TABLES:
        op.execute(f'DROP TABLE IF EXISTS "{t}" CASCADE')


def downgrade() -> None:
    # No recreate — clean baseline only. Restore from archive if needed.
    pass
