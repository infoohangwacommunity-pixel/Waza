"""Request/work correlation IDs for log tracing."""

from __future__ import annotations

from contextvars import ContextVar
from uuid import uuid4

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
execution_id_var: ContextVar[str | None] = ContextVar("execution_id", default=None)


def new_request_id() -> str:
    rid = uuid4().hex[:16]
    request_id_var.set(rid)
    return rid


def set_execution_id(eid: str | None) -> None:
    execution_id_var.set(eid)
