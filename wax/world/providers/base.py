"""Acquisition provider interface — not a package allowlist."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class AcquireRequest:
    kind: str  # python_package | npm_package | binary_prefix | ...
    name: str
    version_spec: str | None = None
    world_id: str = ""


@dataclass
class AcquirePlan:
    steps: list[dict[str, Any]] = field(default_factory=list)
    network_mode: str = "pkg"


@dataclass
class AcquireResult:
    ok: bool
    observed: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    detail: str | None = None


class Provider(Protocol):
    name: str

    def can_handle(self, req: AcquireRequest) -> bool: ...

    async def plan(self, req: AcquireRequest) -> AcquirePlan: ...

    async def install(self, req: AcquireRequest, world_root, plan: AcquirePlan) -> AcquireResult: ...
