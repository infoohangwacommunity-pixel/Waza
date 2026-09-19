"""
WAX Prep durable primitives.

No hardcoded educational modes.
General concepts: Principal, Identity, Conversation, Message,
Work, Execution, Memory, Goal, Activity, Artifact, Schedule.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from wax.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Principal(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """The real person. Independent of any messaging channel."""

    __tablename__ = "principals"

    display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    profile: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    preferences: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, server_default="{}")

    identities: Mapped[list["InterfaceIdentity"]] = relationship(back_populates="principal")
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="principal")
    memories: Mapped[list["Memory"]] = relationship(back_populates="principal")
    works: Mapped[list["Work"]] = relationship(back_populates="principal")
    goals: Mapped[list["Goal"]] = relationship(back_populates="principal")
    artifacts: Mapped[list["Artifact"]] = relationship(back_populates="principal")


class InterfaceIdentity(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Channel identity (WhatsApp number, Telegram id, etc.) linked to a Principal."""

    __tablename__ = "interface_identities"
    __table_args__ = (
        UniqueConstraint("channel", "external_id", name="uq_identity_channel_external"),
        Index("ix_identity_principal", "principal_id"),
    )

    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(50), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, server_default="{}")

    principal: Mapped["Principal"] = relationship(back_populates="identities")


class Conversation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversation_principal", "principal_id"),)

    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="active")
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, server_default="{}")
    last_message_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    principal: Mapped["Principal"] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(back_populates="conversation")


class Message(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_message_conversation", "conversation_id"),
        Index("ix_message_external", "channel", "external_id"),
        UniqueConstraint("channel", "external_id", name="uq_message_channel_external"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(50), nullable=False)
    direction: Mapped[str] = mapped_column(String(20), nullable=False)  # inbound | outbound
    role: Mapped[str] = mapped_column(String(30), nullable=False)  # user | assistant | system
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(String(50), default="text")
    external_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    work_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("works.id", ondelete="SET NULL"), nullable=True
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, server_default="{}")

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")


class InboundEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Raw webhook event for atomic idempotency."""

    __tablename__ = "inbound_events"
    __table_args__ = (
        UniqueConstraint("channel", "external_event_id", name="uq_inbound_event"),
        Index("ix_inbound_processed", "processed"),
    )

    channel: Mapped[str] = mapped_column(String(50), nullable=False)
    external_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    processed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    work_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("works.id", ondelete="SET NULL"), nullable=True
    )
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class Work(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Durable unit of work. Survives process crashes."""

    __tablename__ = "works"
    __table_args__ = (
        Index("ix_work_status", "status"),
        Index("ix_work_principal", "principal_id"),
        Index("ix_work_claim", "status", "claimed_at"),
    )

    principal_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="SET NULL"), nullable=True
    )
    conversation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="queued", nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    objective: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    input_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    result_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_class: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    claimed_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    parent_work_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("works.id", ondelete="SET NULL"), nullable=True
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, server_default="{}")

    principal: Mapped[Optional["Principal"]] = relationship(back_populates="works")
    executions: Mapped[list["Execution"]] = relationship(back_populates="work")
    deliveries: Mapped[list["Delivery"]] = relationship(back_populates="work")


class Execution(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "executions"
    __table_args__ = (
        Index("ix_execution_work", "work_id"),
        Index("ix_execution_status", "status"),
    )

    work_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("works.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(40), default="running", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_class: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, server_default="{}")

    work: Mapped["Work"] = relationship(back_populates="executions")
    tool_executions: Mapped[list["ToolExecution"]] = relationship(back_populates="execution")


class ToolExecution(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "tool_executions"
    __table_args__ = (Index("ix_tool_execution", "execution_id"),)

    execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("executions.id", ondelete="CASCADE"), nullable=False
    )
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    input_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    output_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(40), default="running")
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    observation_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    execution: Mapped["Execution"] = relationship(back_populates="tool_executions")


class Delivery(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "deliveries"
    __table_args__ = (
        Index("ix_delivery_status", "status"),
        Index("ix_delivery_work", "work_id"),
        UniqueConstraint("idempotency_key", name="uq_delivery_idempotency"),
    )

    work_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("works.id", ondelete="SET NULL"), nullable=True
    )
    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(50), nullable=False)
    target_external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(String(50), default="text")
    status: Mapped[str] = mapped_column(String(40), default="pending")
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    external_message_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, server_default="{}")

    work: Mapped[Optional["Work"]] = relationship(back_populates="deliveries")


class Memory(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Multi-level memory: episodic | semantic | learning | preference | goal | behavioral.
    Confidence, provenance, evidence, expiration, supersession.
    """

    __tablename__ = "memories"
    __table_args__ = (
        Index("ix_memory_principal", "principal_id"),
        Index("ix_memory_type", "memory_type"),
        Index("ix_memory_active", "principal_id", "is_active"),
        Index("ix_memory_importance", "importance"),
        Index("ix_memory_expires", "expires_at"),
    )

    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    memory_type: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    structured: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    importance: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    source_message_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    source_work_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("works.id", ondelete="SET NULL"), nullable=True
    )
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memories.id", ondelete="SET NULL"), nullable=True
    )
    embedding: Mapped[Optional[list[float]]] = mapped_column(JSONB, nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    last_confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    validity_status: Mapped[str] = mapped_column(
        String(40), default="active"
    )  # active | historical | uncertain | expired | superseded | contradicted
    contradiction_of_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memories.id", ondelete="SET NULL"), nullable=True
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, server_default="{}")

    principal: Mapped["Principal"] = relationship(back_populates="memories")


class LearningObservation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Granular evolving knowledge-state observations about the learner."""

    __tablename__ = "learning_observations"
    __table_args__ = (
        Index("ix_learnobs_principal", "principal_id"),
        Index("ix_learnobs_concept", "concept_key"),
    )

    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    concept_key: Mapped[str] = mapped_column(String(500), nullable=False)
    concept_label: Mapped[str] = mapped_column(String(500), nullable=False)
    mastery: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.3)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    successes: Mapped[int] = mapped_column(Integer, default=0)
    last_observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    structured: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, server_default="{}")


class Goal(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Dynamic goals that emerge and evolve from interaction."""

    __tablename__ = "goals"
    __table_args__ = (
        Index("ix_goal_principal", "principal_id"),
        Index("ix_goal_status", "status"),
    )

    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="active")
    priority: Mapped[int] = mapped_column(Integer, default=50)
    target_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    structured: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, server_default="{}")

    principal: Mapped["Principal"] = relationship(back_populates="goals")


class Artifact(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Durable generated or uploaded materials."""

    __tablename__ = "artifacts"
    __table_args__ = (Index("ix_artifact_principal", "principal_id"),)

    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    work_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("works.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    storage_path: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    content_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    structured: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(40), default="ready")
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, server_default="{}")

    principal: Mapped["Principal"] = relationship(back_populates="artifacts")


class ScheduledAction(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Database-backed future actions decided by the tutor."""

    __tablename__ = "scheduled_actions"
    __table_args__ = (
        Index("ix_scheduled_due", "status", "execute_at"),
        Index("ix_scheduled_principal", "principal_id"),
    )

    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    action_type: Mapped[str] = mapped_column(String(80), nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    execute_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="pending")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    work_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("works.id", ondelete="SET NULL"), nullable=True
    )
    executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, server_default="{}")


class Concept(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Node in a dynamic knowledge graph — not a hardcoded curriculum tree.
    Concepts emerge from interaction and knowledge sources.
    """

    __tablename__ = "concepts"
    __table_args__ = (
        Index("ix_concept_key", "key", unique=True),
        Index("ix_concept_domain", "domain_key"),
    )

    key: Mapped[str] = mapped_column(String(500), nullable=False)
    label: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    domain_key: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    structured: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, server_default="{}")


class ConceptRelation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Edges: prerequisite_of, related_to, example_of, depends_on,
    commonly_confused_with, demonstrated_by, etc. — open vocabulary.
    """

    __tablename__ = "concept_relations"
    __table_args__ = (
        Index("ix_crel_from", "from_concept_id"),
        Index("ix_crel_to", "to_concept_id"),
        Index("ix_crel_type", "relation_type"),
    )

    from_concept_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False
    )
    to_concept_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False
    )
    relation_type: Mapped[str] = mapped_column(String(80), nullable=False)
    strength: Mapped[float] = mapped_column(Float, default=0.5)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, server_default="{}")


class LearnerConceptState(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Personalized knowledge-graph state for one person against one concept.
    mastery is hypothesis + evidence, not a permanent label.
    """

    __tablename__ = "learner_concept_states"
    __table_args__ = (
        Index("ix_lcs_principal", "principal_id"),
        Index("ix_lcs_concept", "concept_id"),
        Index("ix_lcs_principal_concept", "principal_id", "concept_id", unique=True),
    )

    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    concept_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False
    )
    # open status strings: unfamiliar | partial | theoretical | applied | mastered | rusty | ...
    status: Mapped[str] = mapped_column(String(40), default="unfamiliar")
    mastery: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.2)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    last_observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    structured: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, server_default="{}")


class Activity(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Durable learning activity (assessment, practice session, long task).
    General — not QuizMode. State survives disconnects and restarts.
    """

    __tablename__ = "activities"
    __table_args__ = (
        Index("ix_activity_principal", "principal_id"),
        Index("ix_activity_status", "status"),
    )

    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    work_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("works.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(80), nullable=False)  # assessment | practice | session | other
    objective: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="active")
    # queued | active | waiting | paused | completed | failed | cancelled | expired
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    expected_duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    progress: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    outcome: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, server_default="{}")


class Observation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Raw observations from interactions — not all become durable memories.
    Feeds consolidation and learner-understanding updates.
    """

    __tablename__ = "observations"
    __table_args__ = (
        Index("ix_obs_principal", "principal_id"),
        Index("ix_obs_kind", "kind"),
    )

    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    message_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    work_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("works.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=0.5)
    promoted_to_memory: Mapped[bool] = mapped_column(Boolean, default=False)
    structured: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, server_default="{}")
