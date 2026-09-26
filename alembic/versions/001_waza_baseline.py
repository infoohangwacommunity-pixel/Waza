"""Canonical Waza schema baseline — complete current application models.

Revision ID: 001_waza_baseline
Revises: None

Empty PostgreSQL + alembic upgrade head → full production schema.
Replaces historical 001–017 chain for a clean deployment baseline.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001_waza_baseline"
down_revision: Union[str, None] = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- concepts ---
    op.create_table(
        "concepts",
        sa.Column("key", sa.String(length=500), nullable=False),
        sa.Column("label", sa.String(length=500), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("domain_key", sa.String(length=200), nullable=True),
        sa.Column("structured", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_concept_domain", "concepts", ["domain_key"])
    op.create_index("ix_concept_key", "concepts", ["key"], unique=True)

    # --- principals ---
    op.create_table(
        "principals",
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("profile", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("preferences", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    # --- channel_link_challenges ---
    op.create_table(
        "channel_link_challenges",
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_channel", sa.String(length=40), nullable=False),
        sa.Column("target_channel", sa.String(length=40), nullable=False),
        sa.Column("target_external_id", sa.String(length=255), nullable=False),
        sa.Column("method", sa.String(length=40), nullable=False),
        sa.Column("code_hash", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_link_challenge_principal", "channel_link_challenges", ["principal_id"])
    op.create_index("ix_link_challenge_status", "channel_link_challenges", ["status", "expires_at"])

    # --- concept_relations ---
    op.create_table(
        "concept_relations",
        sa.Column("from_concept_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("to_concept_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("relation_type", sa.String(length=80), nullable=False),
        sa.Column("strength", sa.Float(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_crel_type", "concept_relations", ["relation_type"])
    op.create_index("ix_crel_to", "concept_relations", ["to_concept_id"])
    op.create_index("ix_crel_from", "concept_relations", ["from_concept_id"])

    # --- conversations ---
    op.create_table(
        "conversations",
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("summary", sa.String(), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_conversation_principal", "conversations", ["principal_id"])

    # --- goals ---
    op.create_table(
        "goals",
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("target_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("structured", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_goal_status", "goals", ["status"])
    op.create_index("ix_goal_principal", "goals", ["principal_id"])

    # --- interface_identities ---
    op.create_table(
        "interface_identities",
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", sa.String(length=50), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("channel", "external_id", name="uq_identity_channel_external"),
    )
    op.create_index("ix_identity_principal", "interface_identities", ["principal_id"])

    # --- knowledge_sources ---
    op.create_table(
        "knowledge_sources",
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("storage_path", sa.String(length=1000), nullable=True),
        sa.Column("content_type", sa.String(length=100), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("text_extract", sa.String(), nullable=True),
        sa.Column("structured", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_ks_principal", "knowledge_sources", ["principal_id"])

    # --- learner_concept_states ---
    op.create_table(
        "learner_concept_states",
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("concept_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("mastery", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("structured", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("stability", sa.Float(), nullable=False),
        sa.Column("difficulty", sa.Float(), nullable=False),
        sa.Column("retrievability", sa.Float(), nullable=True),
        sa.Column("lapse_count", sa.Integer(), nullable=False),
        sa.Column("last_review_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_review_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("independent_successes", sa.Integer(), nullable=False),
        sa.Column("assisted_successes", sa.Integer(), nullable=False),
        sa.Column("review_count", sa.Integer(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_lcs_principal", "learner_concept_states", ["principal_id"])
    op.create_index("ix_lcs_concept", "learner_concept_states", ["concept_id"])
    op.create_index("ix_lcs_principal_concept", "learner_concept_states", ["principal_id", "concept_id"], unique=True)

    # --- learning_observations ---
    op.create_table(
        "learning_observations",
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("concept_key", sa.String(length=500), nullable=False),
        sa.Column("concept_label", sa.String(length=500), nullable=False),
        sa.Column("mastery", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("successes", sa.Integer(), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("structured", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_learnobs_concept", "learning_observations", ["concept_key"])
    op.create_index("ix_learnobs_principal", "learning_observations", ["principal_id"])

    # --- misconceptions ---
    op.create_table(
        "misconceptions",
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("concept_key", sa.String(length=500), nullable=True),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved", sa.Boolean(), nullable=False),
        sa.Column("resolution_evidence", postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_misc_resolved", "misconceptions", ["principal_id", "resolved"])
    op.create_index("ix_misc_principal", "misconceptions", ["principal_id"])

    # --- principal_workloads ---
    op.create_table(
        "principal_workloads",
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tokens", sa.Float(), nullable=False),
        sa.Column("last_refill_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_count", sa.Integer(), nullable=False),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checkin_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checkin_week_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checkin_week_count", sa.Integer(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("principal_id", name="uq_principal_workload"),
    )
    op.create_index("ix_principal_workload_principal", "principal_workloads", ["principal_id"])

    # --- worlds ---
    op.create_table(
        "worlds",
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lifecycle_state", sa.String(length=40), nullable=False),
        sa.Column("lifecycle_reason", sa.String(), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("disk_used_bytes", sa.Integer(), nullable=True),
        sa.Column("env_size_bytes", sa.Integer(), nullable=True),
        sa.Column("root_path", sa.String(length=1000), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("principal_id", name="uq_world_principal"),
    )
    op.create_index("ix_world_lifecycle", "worlds", ["lifecycle_state"])

    # --- document_chunks ---
    op.create_table(
        "document_chunks",
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("knowledge_source_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("knowledge_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("embedding", postgresql.JSONB(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_chunk_principal", "document_chunks", ["principal_id"])
    op.create_index("ix_chunk_source", "document_chunks", ["knowledge_source_id"])

    # --- works ---
    op.create_table(
        "works",
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="SET NULL"), nullable=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("kind", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("objective", sa.String(), nullable=True),
        sa.Column("input_payload", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("result_payload", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("error_class", sa.String(length=100), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("claimed_by", sa.String(length=100), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("parent_work_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_work_status", "works", ["status"])
    op.create_index("ix_work_principal", "works", ["principal_id"])
    op.create_index("ix_work_claim", "works", ["status", "claimed_at"])
    op.create_foreign_key("fk_works_parent_work_id", "works", "works", ["parent_work_id"], ["id"], ondelete="SET NULL")

    # --- activities ---
    op.create_table(
        "activities",
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("work_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("works.id", ondelete="SET NULL"), nullable=True),
        sa.Column("kind", sa.String(length=80), nullable=False),
        sa.Column("objective", sa.String(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expected_duration_seconds", sa.Integer(), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("progress", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("outcome", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_activity_status", "activities", ["status"])
    op.create_index("ix_activity_principal", "activities", ["principal_id"])

    # --- artifacts ---
    op.create_table(
        "artifacts",
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("work_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("works.id", ondelete="SET NULL"), nullable=True),
        sa.Column("kind", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("storage_path", sa.String(length=1000), nullable=True),
        sa.Column("content_type", sa.String(length=100), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("content", sa.String(), nullable=True),
        sa.Column("structured", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_artifact_principal", "artifacts", ["principal_id"])

    # --- deliveries ---
    op.create_table(
        "deliveries",
        sa.Column("work_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("works.id", ondelete="SET NULL"), nullable=True),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", sa.String(length=50), nullable=False),
        sa.Column("target_external_id", sa.String(length=255), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("content_type", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("external_message_id", sa.String(length=255), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("idempotency_key", name="uq_delivery_idempotency"),
    )
    op.create_index("ix_delivery_work", "deliveries", ["work_id"])
    op.create_index("ix_delivery_status", "deliveries", ["status"])

    # --- executions ---
    op.create_table(
        "executions",
        sa.Column("work_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("works.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checkpoint", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("steps", postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("error_class", sa.String(length=100), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_execution_work", "executions", ["work_id"])
    op.create_index("ix_execution_status", "executions", ["status"])

    # --- inbound_events ---
    op.create_table(
        "inbound_events",
        sa.Column("channel", sa.String(length=50), nullable=False),
        sa.Column("external_event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("processed", sa.Boolean(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("work_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("works.id", ondelete="SET NULL"), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("channel", "external_event_id", name="uq_inbound_event"),
    )
    op.create_index("ix_inbound_processed", "inbound_events", ["processed"])

    # --- messages ---
    op.create_table(
        "messages",
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", sa.String(length=50), nullable=False),
        sa.Column("direction", sa.String(length=20), nullable=False),
        sa.Column("role", sa.String(length=30), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("content_type", sa.String(length=50), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("work_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("works.id", ondelete="SET NULL"), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("channel", "external_id", name="uq_message_channel_external"),
    )
    op.create_index("ix_message_conversation", "messages", ["conversation_id"])
    op.create_index("ix_message_external", "messages", ["channel", "external_id"])

    # --- publications ---
    op.create_table(
        "publications",
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("work_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("works.id", ondelete="SET NULL"), nullable=True),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleaned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("schema_version", sa.String(length=20), nullable=False),
        sa.Column("renderer_version", sa.String(length=20), nullable=False),
        sa.Column("semantic", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("storage_uri", sa.String(length=1000), nullable=True),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("parent_publication_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("artifact_ids", postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column("access_count", sa.Integer(), nullable=False),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_publication_lifecycle", "publications", ["status", "expires_at"])
    op.create_index("ix_publication_cleanup", "publications", ["status", "cleaned_at"])
    op.create_index("ix_publication_token_hash", "publications", ["token_hash"], unique=True)
    op.create_index("ix_publication_principal", "publications", ["principal_id"])
    op.create_foreign_key("fk_publications_parent_publication_id", "publications", "publications", ["parent_publication_id"], ["id"], ondelete="SET NULL")

    # --- scheduled_actions ---
    op.create_table(
        "scheduled_actions",
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action_type", sa.String(length=80), nullable=False),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("execute_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("result", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("work_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("works.id", ondelete="SET NULL"), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("idempotency_key", name="uq_scheduled_actions_idempotency_key"),
    )
    op.create_index("ix_scheduled_principal", "scheduled_actions", ["principal_id"])
    op.create_index("ix_scheduled_due", "scheduled_actions", ["status", "execute_at"])

    # --- surfaces ---
    op.create_table(
        "surfaces",
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("work_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("works.id", ondelete="SET NULL"), nullable=True),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleaned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_learner_interaction_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_ai_request_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_ai_response_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_revision_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retention_requested", sa.Boolean(), nullable=False),
        sa.Column("related_surface_ids", postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column("current_revision", sa.Integer(), nullable=False),
        sa.Column("granted_scopes", postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column("parent_surface_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("access_count", sa.Integer(), nullable=False),
        sa.Column("lifecycle_intent", sa.String(length=80), nullable=True),
        sa.Column("state_json", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_surface_token_hash", "surfaces", ["token_hash"], unique=True)
    op.create_index("ix_surface_activity", "surfaces", ["status", "last_activity_at"])
    op.create_index("ix_surface_lifecycle", "surfaces", ["status", "expires_at"])
    op.create_index("ix_surface_principal", "surfaces", ["principal_id"])
    op.create_index("ix_surface_idempotency", "surfaces", ["principal_id", "idempotency_key"], unique=True)
    op.create_foreign_key("fk_surfaces_parent_surface_id", "surfaces", "surfaces", ["parent_surface_id"], ["id"], ondelete="SET NULL")

    # --- assessments ---
    op.create_table(
        "assessments",
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("activity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("activities.id", ondelete="SET NULL"), nullable=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("objective", sa.String(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("timed", sa.Boolean(), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settings", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_assessment_principal", "assessments", ["principal_id"])
    op.create_index("ix_assessment_status", "assessments", ["status"])

    # --- interactions ---
    op.create_table(
        "interactions",
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("activity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("activities.id", ondelete="SET NULL"), nullable=True),
        sa.Column("work_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("works.id", ondelete="SET NULL"), nullable=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("channel", sa.String(length=40), nullable=False),
        sa.Column("callback_token", sa.String(length=64), nullable=False),
        sa.Column("external_message_id", sa.String(length=128), nullable=True),
        sa.Column("prompt", sa.String(), nullable=True),
        sa.Column("style", sa.String(length=40), nullable=False),
        sa.Column("choices", postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_choice_id", sa.String(length=256), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("callback_token", name="uq_interaction_callback_token"),
    )
    op.create_index("ix_interaction_principal", "interactions", ["principal_id"])
    op.create_index("ix_interaction_status_expires", "interactions", ["status", "expires_at"])

    # --- memories ---
    op.create_table(
        "memories",
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("memory_type", sa.String(length=50), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("structured", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("importance", sa.Float(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("source_message_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_work_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("works.id", ondelete="SET NULL"), nullable=True),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("embedding", postgresql.JSONB(), nullable=True),
        sa.Column("tags", postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("validity_status", sa.String(length=40), nullable=False),
        sa.Column("contradiction_of_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_memory_importance", "memories", ["importance"])
    op.create_index("ix_memory_active", "memories", ["principal_id", "is_active"])
    op.create_index("ix_memory_expires", "memories", ["expires_at"])
    op.create_index("ix_memory_principal", "memories", ["principal_id"])
    op.create_index("ix_memory_type", "memories", ["memory_type"])
    op.create_foreign_key("fk_memories_superseded_by_id", "memories", "memories", ["superseded_by_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_memories_contradiction_of_id", "memories", "memories", ["contradiction_of_id"], ["id"], ondelete="SET NULL")

    # --- observations ---
    op.create_table(
        "observations",
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("work_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("works.id", ondelete="SET NULL"), nullable=True),
        sa.Column("kind", sa.String(length=80), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("promoted_to_memory", sa.Boolean(), nullable=False),
        sa.Column("structured", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_obs_principal", "observations", ["principal_id"])
    op.create_index("ix_obs_kind", "observations", ["kind"])

    # --- surface_ai_requests ---
    op.create_table(
        "surface_ai_requests",
        sa.Column("surface_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("surfaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_token_hash", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("message_preview", sa.String(length=240), nullable=True),
        sa.Column("reply_preview", sa.String(), nullable=True),
        sa.Column("work_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("works.id", ondelete="SET NULL"), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column("error", sa.String(length=300), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("request_token_hash", name="uq_surface_ai_request_token"),
    )
    op.create_index("uq_surface_ai_req_idempotency", "surface_ai_requests", ["surface_id", "idempotency_key"], unique=True)
    op.create_index("ix_surface_ai_req_status", "surface_ai_requests", ["status"])
    op.create_index("ix_surface_ai_req_surface", "surface_ai_requests", ["surface_id", "created_at"])

    # --- surface_revisions ---
    op.create_table(
        "surface_revisions",
        sa.Column("surface_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("surfaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("entry_uri", sa.String(length=1000), nullable=True),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("checksum", sa.String(length=128), nullable=True),
        sa.Column("manifest", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("source_note", sa.String(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("surface_id", "revision", name="uq_surface_revision"),
    )
    op.create_index("ix_surface_revision_surface", "surface_revisions", ["surface_id"])

    # --- surface_sessions ---
    op.create_table(
        "surface_sessions",
        sa.Column("surface_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("surfaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_token_hash", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_surface_session_surface", "surface_sessions", ["surface_id"])
    op.create_index("ix_surface_session_token", "surface_sessions", ["session_token_hash"], unique=True)
    op.create_index("ix_surface_session_expires", "surface_sessions", ["expires_at"])

    # --- temporal_intents ---
    op.create_table(
        "temporal_intents",
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("purpose", sa.String(length=80), nullable=False),
        sa.Column("target", sa.String(), nullable=False),
        sa.Column("execute_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("urgency", sa.String(length=40), nullable=False),
        sa.Column("flexibility", sa.String(length=40), nullable=False),
        sa.Column("completion_condition", sa.String(), nullable=True),
        sa.Column("original_request", sa.String(), nullable=True),
        sa.Column("original_context", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("scheduled_action_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("scheduled_actions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("concept_key", sa.String(length=500), nullable=True),
        sa.Column("goal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("goals.id", ondelete="SET NULL"), nullable=True),
        sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evaluation", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("idempotency_key", name="uq_temporal_intents_idempotency_key"),
    )
    op.create_index("ix_temporal_intent_due", "temporal_intents", ["status", "execute_at"])
    op.create_index("ix_temporal_intent_principal", "temporal_intents", ["principal_id", "status"])

    # --- tool_executions ---
    op.create_table(
        "tool_executions",
        sa.Column("execution_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("executions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tool_name", sa.String(length=100), nullable=False),
        sa.Column("input_payload", postgresql.JSONB(), nullable=False),
        sa.Column("output_payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("observation_summary", sa.String(), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_tool_execution", "tool_executions", ["execution_id"])

    # --- assessment_attempts ---
    op.create_table(
        "assessment_attempts",
        sa.Column("assessment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("max_score", sa.Float(), nullable=True),
        sa.Column("summary", sa.String(), nullable=True),
        sa.Column("structured", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_aattempt_principal", "assessment_attempts", ["principal_id"])
    op.create_index("ix_aattempt_assessment", "assessment_attempts", ["assessment_id"])

    # --- assessment_items ---
    op.create_table(
        "assessment_items",
        sa.Column("assessment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("item_type", sa.String(length=40), nullable=False),
        sa.Column("prompt", sa.String(), nullable=False),
        sa.Column("options", postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column("answer_key", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("points", sa.Float(), nullable=False),
        sa.Column("structured", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_aitem_assessment", "assessment_items", ["assessment_id"])

    # --- hypotheses ---
    op.create_table(
        "hypotheses",
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("claim_key", sa.String(length=500), nullable=False),
        sa.Column("claim", sa.String(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("rationale", sa.String(), nullable=True),
        sa.Column("supporting_evidence_ids", postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column("contradicting_evidence_ids", postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column("first_formed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("memory_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("memories.id", ondelete="SET NULL"), nullable=True),
        sa.Column("structured", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_hyp_claim", "hypotheses", ["claim_key"])
    op.create_index("ix_hyp_principal", "hypotheses", ["principal_id"])
    op.create_index("ix_hyp_status", "hypotheses", ["status"])
    op.create_foreign_key("fk_hypotheses_superseded_by_id", "hypotheses", "hypotheses", ["superseded_by_id"], ["id"], ondelete="SET NULL")

    # --- surface_events ---
    op.create_table(
        "surface_events",
        sa.Column("surface_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("surfaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("surface_sessions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("revision", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_surface_event_surface", "surface_events", ["surface_id", "created_at"])
    op.create_index("ix_surface_event_type", "surface_events", ["event_type"])

    # --- assessment_responses ---
    op.create_table(
        "assessment_responses",
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("assessment_attempts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("assessment_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("response_text", sa.String(), nullable=True),
        sa.Column("response_structured", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("is_correct", sa.Boolean(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("feedback", sa.String(), nullable=True),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_aresp_attempt", "assessment_responses", ["attempt_id"])
    op.create_index("ix_aresp_item", "assessment_responses", ["item_id"])

    # --- evidence ---
    op.create_table(
        "evidence",
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("evidence_type", sa.String(length=80), nullable=False),
        sa.Column("claim_key", sa.String(length=500), nullable=True),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("assistance_level", sa.String(length=40), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("directness", sa.Float(), nullable=False),
        sa.Column("independence", sa.Float(), nullable=False),
        sa.Column("specificity", sa.Float(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("observation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("observations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("work_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("works.id", ondelete="SET NULL"), nullable=True),
        sa.Column("assessment_response_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("assessment_responses.id", ondelete="SET NULL"), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_evidence_kind", "evidence", ["evidence_type"])
    op.create_index("ix_evidence_claim", "evidence", ["claim_key"])
    op.create_index("ix_evidence_principal", "evidence", ["principal_id"])

    # --- learning_events ---
    op.create_table(
        "learning_events",
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=80), nullable=False),
        sa.Column("summary", sa.String(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column("concept_key", sa.String(length=500), nullable=True),
        sa.Column("goal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("goals.id", ondelete="SET NULL"), nullable=True),
        sa.Column("activity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("activities.id", ondelete="SET NULL"), nullable=True),
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("evidence.id", ondelete="SET NULL"), nullable=True),
        sa.Column("memory_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("memories.id", ondelete="SET NULL"), nullable=True),
        sa.Column("scheduled_action_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("scheduled_actions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("work_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("works.id", ondelete="SET NULL"), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_learning_event_kind", "learning_events", ["principal_id", "kind"])
    op.create_index("ix_learning_event_principal_time", "learning_events", ["principal_id", "observed_at"])
    op.create_index("ix_learning_event_concept", "learning_events", ["principal_id", "concept_key"])


def downgrade() -> None:
    op.drop_table("learning_events")
    op.drop_table("evidence")
    op.drop_table("assessment_responses")
    op.drop_table("surface_events")
    op.drop_table("hypotheses")
    op.drop_table("assessment_items")
    op.drop_table("assessment_attempts")
    op.drop_table("tool_executions")
    op.drop_table("temporal_intents")
    op.drop_table("surface_sessions")
    op.drop_table("surface_revisions")
    op.drop_table("surface_ai_requests")
    op.drop_table("observations")
    op.drop_table("memories")
    op.drop_table("interactions")
    op.drop_table("assessments")
    op.drop_table("surfaces")
    op.drop_table("scheduled_actions")
    op.drop_table("publications")
    op.drop_table("messages")
    op.drop_table("inbound_events")
    op.drop_table("executions")
    op.drop_table("deliveries")
    op.drop_table("artifacts")
    op.drop_table("activities")
    op.drop_table("works")
    op.drop_table("document_chunks")
    op.drop_table("worlds")
    op.drop_table("principal_workloads")
    op.drop_table("misconceptions")
    op.drop_table("learning_observations")
    op.drop_table("learner_concept_states")
    op.drop_table("knowledge_sources")
    op.drop_table("interface_identities")
    op.drop_table("goals")
    op.drop_table("conversations")
    op.drop_table("concept_relations")
    op.drop_table("channel_link_challenges")
    op.drop_table("principals")
    op.drop_table("concepts")

