"""Materialize core tables with explicit create + hard verification.

Context:
- 001/002/005/006 used Base.metadata.create_all(checkfirst=True).
- Production can reach a revision while relation "works" is still missing
  (stamped revisions, failed create_all, or JSONB DEFAULT '{}' issues).
- This revision creates any missing model tables, then FAILS the deploy
  if required core tables (including works) are still absent.

Revision ID: 007
Revises: 006
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels = None
depends_on = None

REQUIRED_CORE = (
    "principals",
    "interface_identities",
    "conversations",
    "messages",
    "inbound_events",
    "works",
    "executions",
    "tool_executions",
    "deliveries",
    "memories",
    "goals",
    "artifacts",
    "scheduled_actions",
    "concepts",
    "learner_concept_states",
    "activities",
    "evidence",
    "hypotheses",
    "assessments",
)


def _existing_tables(bind) -> set[str]:
    return set(inspect(bind).get_table_names(schema="public"))


def _fix_jsonb_defaults(table) -> None:
    """Rewrite bare '{}' / '[]' server defaults to proper jsonb casts.

    SQLAlchemy may emit DEFAULT '{}' (text) for JSONB columns, which
    PostgreSQL rejects. Mutate in-place before table.create().
    """
    for col in table.columns:
        sd = col.server_default
        if sd is None:
            continue
        arg = getattr(sd, "arg", None)
        if arg is None:
            continue
        if isinstance(arg, str) and arg in ("{}", "[]"):
            col.server_default = text(f"'{arg}'::jsonb")


def upgrade() -> None:
    from wax.db.base import Base
    import wax.db.models  # noqa: F401

    bind = op.get_bind()

    before = _existing_tables(bind)
    print(f"alembic_007_before count={len(before)} tables={sorted(before)}")

    created: list[str] = []
    errors: list[str] = []
    for table in Base.metadata.sorted_tables:
        if table.name in before:
            continue
        _fix_jsonb_defaults(table)
        try:
            print(f"alembic_007_creating table={table.name}")
            table.create(bind, checkfirst=True)
            created.append(table.name)
        except Exception as e:
            msg = f"{table.name}: {type(e).__name__}: {e}"
            print(f"alembic_007_create_error {msg}")
            errors.append(msg)

    after = _existing_tables(bind)
    print(f"alembic_007_after count={len(after)} created={created}")

    # Fallback: explicit CREATE for works if still missing (the reported failure).
    if "works" not in after:
        print("alembic_007_fallback_explicit_works")
        bind.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS public.works (
                    id UUID NOT NULL,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
                    principal_id UUID REFERENCES public.principals(id) ON DELETE SET NULL,
                    conversation_id UUID REFERENCES public.conversations(id) ON DELETE SET NULL,
                    kind VARCHAR(80) NOT NULL,
                    status VARCHAR(40) NOT NULL DEFAULT 'queued',
                    priority INTEGER NOT NULL DEFAULT 100,
                    objective TEXT,
                    input_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    result_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    error TEXT,
                    error_class VARCHAR(100),
                    attempt INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 5,
                    claimed_by VARCHAR(100),
                    claimed_at TIMESTAMP WITH TIME ZONE,
                    started_at TIMESTAMP WITH TIME ZONE,
                    completed_at TIMESTAMP WITH TIME ZONE,
                    next_retry_at TIMESTAMP WITH TIME ZONE,
                    parent_work_id UUID REFERENCES public.works(id) ON DELETE SET NULL,
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                    CONSTRAINT pk_works PRIMARY KEY (id)
                )
                """
            )
        )
        bind.execute(text("CREATE INDEX IF NOT EXISTS ix_work_status ON public.works (status)"))
        bind.execute(text("CREATE INDEX IF NOT EXISTS ix_work_principal ON public.works (principal_id)"))
        bind.execute(text("CREATE INDEX IF NOT EXISTS ix_work_claim ON public.works (status, claimed_at)"))
        after = _existing_tables(bind)

    missing = [t for t in REQUIRED_CORE if t not in after]
    works_reg = bind.execute(text("SELECT to_regclass('public.works')")).scalar()
    print(f"alembic_007_works_regclass={works_reg}")

    if missing or works_reg is None:
        detail = ", ".join(missing) if missing else "works regclass null"
        err_detail = "; ".join(errors[:5]) if errors else "none"
        raise RuntimeError(
            f"Migration 007 failed: required tables still missing ({detail}). "
            f"create_errors=[{err_detail}]"
        )

    print("alembic_007_ok")


def downgrade() -> None:
    pass
