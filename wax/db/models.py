"""
WAX durable reality (not educational theory tables).

Identity & channels: Principal, InterfaceIdentity
  (ChannelLinkChallenge table may still exist from baseline; feature code removed)
Conversation: Conversation, Message, InboundEvent
World: World
Durable state: Memory (AI-owned; goals live here as memory_type, not a Goal engine)
Files: Artifact
Time: ScheduledAction
Work: Work, Execution, Delivery
Interactive chat: Interaction
Temporary web workspace: Surface + revision/session/event/ai_request
Security/rate: PrincipalWorkload
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
    artifacts: Mapped[list["Artifact"]] = relationship(back_populates="principal")
    surfaces: Mapped[list["Surface"]] = relationship(back_populates="principal")



class World(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Personal computing world — durable system state in Postgres.
    Volume holds files/runtimes; this row is identity + lifecycle authority.
    world_id is independent of principal_id (owner).
    """

    __tablename__ = "worlds"
    __table_args__ = (
        UniqueConstraint("principal_id", name="uq_world_principal"),
        Index("ix_world_lifecycle", "lifecycle_state"),
    )

    # independent world identity is the PK (id from UUIDPrimaryKeyMixin)
    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    lifecycle_state: Mapped[str] = mapped_column(String(40), default="READY", nullable=False)
    lifecycle_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    disk_used_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    env_size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    root_path: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, server_default="{}")


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
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=True
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
    """Durable student state row. AI owns lifecycle; infrastructure stores and isolates."""

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
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memories.id", ondelete="SET NULL"), nullable=True
    )
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
    """Future wake decided by the AI; infrastructure stores and fires Work."""

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










class Interaction(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Server-authoritative interactive choice (buttons/lists).

    Channel adapters only render; consume/expire happen here.
    Opaque callback_token is what platforms send back (Telegram limit 64 bytes).
    """

    __tablename__ = "interactions"
    __table_args__ = (
        Index("ix_interaction_principal", "principal_id"),
        Index("ix_interaction_status_expires", "status", "expires_at"),
        UniqueConstraint("callback_token", name="uq_interaction_callback_token"),
    )

    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    work_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("works.id", ondelete="SET NULL"), nullable=True
    )
    conversation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    channel: Mapped[str] = mapped_column(String(40), nullable=False)
    callback_token: Mapped[str] = mapped_column(String(64), nullable=False)
    external_message_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    style: Mapped[str] = mapped_column(String(40), default="buttons")
    choices: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    status: Mapped[str] = mapped_column(String(40), default="pending")
    # pending | consumed | expired | cancelled
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    consumed_choice_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, server_default="{}")






class ChannelLinkChallenge(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Challenge to authorize linking another channel identity to a principal."""

    __tablename__ = "channel_link_challenges"
    __table_args__ = (
        Index("ix_link_challenge_principal", "principal_id"),
        Index("ix_link_challenge_status", "status", "expires_at"),
    )

    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    source_channel: Mapped[str] = mapped_column(String(40), nullable=False)
    target_channel: Mapped[str] = mapped_column(String(40), nullable=False)
    target_external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    method: Mapped[str] = mapped_column(String(40), default="otp")  # otp | knowledge
    code_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="pending")
    # pending | verified | expired | failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, server_default="{}")









class Surface(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    AI-authored temporary web environment.

    Identity is stable across renames and revisions.
    Public access is via opaque token only — never expose internal IDs in the browser URL.
    """

    __tablename__ = "surfaces"
    __table_args__ = (
        Index("ix_surface_principal", "principal_id"),
        Index("ix_surface_token_hash", "token_hash", unique=True),
        Index("ix_surface_lifecycle", "status", "expires_at"),
        Index("ix_surface_activity", "status", "last_activity_at"),
        Index("ix_surface_idempotency", "principal_id", "idempotency_key", unique=True),
    )

    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    work_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("works.id", ondelete="SET NULL"), nullable=True
    )
    # Opaque public capability (hashed)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    # AI-controlled human title (rename does not change URL)
    title: Mapped[str] = mapped_column(String(500), nullable=False, default="Surface")
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Lifecycle
    status: Mapped[str] = mapped_column(String(40), default="creating", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    cleaned_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_activity_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_opened_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_user_interaction_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_ai_request_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_ai_response_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_revision_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    retention_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    related_surface_ids: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    # Current revision pointer
    current_revision: Mapped[int] = mapped_column(Integer, default=0)
    # Capability grants (server-side list of scopes)
    granted_scopes: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    # Idempotency for create
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    # Lineage
    parent_surface_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("surfaces.id", ondelete="SET NULL"), nullable=True
    )
    # Soft counters
    access_count: Mapped[int] = mapped_column(Integer, default=0)
    # AI lifecycle intent (hint only)
    lifecycle_intent: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    # Runtime state blob ref (small JSON) — large state in object storage
    state_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, server_default="{}")

    principal: Mapped["Principal"] = relationship(back_populates="surfaces")


class SurfaceRevision(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Immutable AI-generated experience bundle for a surface."""

    __tablename__ = "surface_revisions"
    __table_args__ = (
        UniqueConstraint("surface_id", "revision", name="uq_surface_revision"),
        Index("ix_surface_revision_surface", "surface_id"),
    )

    surface_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("surfaces.id", ondelete="CASCADE"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    # Entry point storage (HTML bundle)
    entry_uri: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    content_type: Mapped[str] = mapped_column(String(100), default="text/html; charset=utf-8")
    size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    checksum: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # Manifest: assets, required capabilities, runtime policy
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    # Optional source note from AI (not shown to learner unless intended)
    source_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, server_default="{}")


class SurfaceSession(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Browser session bound to a surface + principal."""

    __tablename__ = "surface_sessions"
    __table_args__ = (
        Index("ix_surface_session_surface", "surface_id"),
        Index("ix_surface_session_token", "session_token_hash", unique=True),
        Index("ix_surface_session_expires", "expires_at"),
    )

    surface_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("surfaces.id", ondelete="CASCADE"), nullable=False
    )
    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    session_token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    scopes: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, server_default="{}")


class SurfaceEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Generic surface interaction event — not an educational taxonomy."""

    __tablename__ = "surface_events"
    __table_args__ = (
        Index("ix_surface_event_surface", "surface_id", "created_at"),
        Index("ix_surface_event_type", "event_type"),
    )

    surface_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("surfaces.id", ondelete="CASCADE"), nullable=False
    )
    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("surface_sessions.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    # opened | interaction | state_changed | ai_requested | save_requested | error | session_started | ...
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    revision: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(40), default="browser")



class SurfaceAiRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Opaque surface→intelligence request. Browser sees request_id, never work_id."""

    __tablename__ = "surface_ai_requests"
    __table_args__ = (
        Index("ix_surface_ai_req_surface", "surface_id", "created_at"),
        UniqueConstraint("request_token_hash", name="uq_surface_ai_request_token"),
        Index("ix_surface_ai_req_status", "status"),
        Index("uq_surface_ai_req_idempotency", "surface_id", "idempotency_key", unique=True),
    )

    surface_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("surfaces.id", ondelete="CASCADE"), nullable=False
    )
    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    request_token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="accepted")
    # accepted | processing | completed | failed
    message_preview: Mapped[Optional[str]] = mapped_column(String(240), nullable=True)
    reply_preview: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    work_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("works.id", ondelete="SET NULL"), nullable=True
    )
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, server_default="{}")


class PrincipalWorkload(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Durable per-principal workload / guardrail state.

    Used for burst-tolerant rate protection across workers.
    Message durability does not depend on this row.
    """

    __tablename__ = "principal_workloads"
    __table_args__ = (
        UniqueConstraint("principal_id", name="uq_principal_workload"),
        Index("ix_principal_workload_principal", "principal_id"),
    )

    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    tokens: Mapped[float] = mapped_column(Float, default=12.0, nullable=False)
    last_refill_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    window_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    window_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cooldown_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, server_default="{}")
