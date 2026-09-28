"""Per-turn operational telemetry — not an intelligence tool counter."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from wax.observability.logging import get_logger

logger = get_logger(__name__)

_current: ContextVar["TurnTelemetry | None"] = ContextVar("turn_telemetry", default=None)


@dataclass
class TurnTelemetry:
    work_id: str | None = None
    principal_id: str | None = None
    channel: str | None = None
    continuations: int = 0
    directives_run: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "work_id": self.work_id,
            "principal_id": self.principal_id,
            "channel": self.channel,
            "continuations": self.continuations,
            "directives_run": self.directives_run,
            **self.extra,
        }

    def emit(self) -> None:
        data = {k: v for k, v in self.as_dict().items() if v is not None and v != "" and v != 0}
        if data:
            logger.info("turn_telemetry", **data)


def begin_turn(**kwargs: Any) -> TurnTelemetry:
    tel = TurnTelemetry(**{k: v for k, v in kwargs.items() if hasattr(TurnTelemetry, k) or k in ("work_id", "principal_id", "channel")})
    for k, v in kwargs.items():
        if k not in ("work_id", "principal_id", "channel") and not hasattr(tel, k):
            tel.extra[k] = v
    _current.set(tel)
    return tel


def get_turn() -> TurnTelemetry | None:
    return _current.get()


def end_turn() -> None:
    tel = _current.get()
    if tel:
        tel.emit()
    _current.set(None)
