
"""Per-work token / call accounting — one cycle of intelligence → delivery."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from wax.observability.events import emit
from wax.observability.logging import get_logger

logger = get_logger(__name__)

_current: ContextVar["TurnTelemetry | None"] = ContextVar("turn_telemetry", default=None)


@dataclass
class TurnTelemetry:
    work_id: str = ""
    principal_id: str = ""
    channel: str = ""
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tools_exposed: int = 0
    tools_called: int = 0
    orchestration_path: str = ""
    capability_families: list[str] = field(default_factory=list)
    stages: list[str] = field(default_factory=list)

    def record_completion(
        self,
        *,
        provider: str = "",
        model: str = "",
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        role: str = "",
    ) -> None:
        self.model_calls += 1
        if input_tokens:
            self.input_tokens += int(input_tokens)
        if output_tokens:
            self.output_tokens += int(output_tokens)
        stage = f"{role or 'model'}:{provider}:{model}"
        self.stages.append(stage[:80])

    def snapshot(self) -> dict[str, Any]:
        return {
            "work_id": self.work_id,
            "principal_id": self.principal_id,
            "channel": self.channel,
            "model_calls": self.model_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "tools_exposed": self.tools_exposed,
            "tools_called": self.tools_called,
            "orchestration_path": self.orchestration_path,
            "capability_families": list(self.capability_families),
            "stages": list(self.stages)[:20],
        }

    def finish(self, *, outcome: str = "completed") -> None:
        data = self.snapshot()
        data["outcome"] = outcome
        logger.info("turn_telemetry", **data)
        emit("turn.completed", **data)


def begin_turn(
    *,
    work_id: str = "",
    principal_id: str = "",
    channel: str = "",
) -> TurnTelemetry:
    tel = TurnTelemetry(
        work_id=work_id or "",
        principal_id=principal_id or "",
        channel=channel or "",
    )
    _current.set(tel)
    emit(
        "turn.started",
        work_id=tel.work_id,
        principal_id=tel.principal_id,
        channel=tel.channel,
    )
    return tel


def get_turn() -> TurnTelemetry | None:
    return _current.get()


def end_turn(outcome: str = "completed") -> None:
    tel = _current.get()
    if tel:
        tel.finish(outcome=outcome)
    _current.set(None)
