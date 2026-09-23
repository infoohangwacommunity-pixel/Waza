"""Structured World errors — Intelligence can reason about next actions."""

from __future__ import annotations


class WorldError(Exception):
    code: str = "WORLD_ERROR"

    def __init__(self, message: str = "", *, detail: str | None = None):
        self.message = message or self.code
        self.detail = detail
        super().__init__(self.message)

    def to_dict(self) -> dict:
        d = {"ok": False, "error": self.code, "message": self.message}
        if self.detail:
            d["detail"] = self.detail[:500]
        return d


class WorldNotReady(WorldError):
    code = "WORLD_NOT_READY"


class WorldDegraded(WorldError):
    code = "WORLD_DEGRADED"


class WorldRecoveryRequired(WorldError):
    code = "WORLD_RECOVERY_REQUIRED"


class CapabilityMissing(WorldError):
    code = "CAPABILITY_MISSING"


class AcquisitionUnavailable(WorldError):
    code = "ACQUISITION_UNAVAILABLE"


class AcquisitionFailed(WorldError):
    code = "ACQUISITION_FAILED"


class VerificationFailed(WorldError):
    code = "VERIFICATION_FAILED"


class ResourceDenied(WorldError):
    code = "RESOURCE_DENIED"


class QuotaExceeded(WorldError):
    code = "QUOTA_EXCEEDED"


class ExecutionTimeout(WorldError):
    code = "EXECUTION_TIMEOUT"


class ExecutionCancelled(WorldError):
    code = "EXECUTION_CANCELLED"


class IsolationUnavailable(WorldError):
    code = "ISOLATION_UNAVAILABLE"


class PolicyDenied(WorldError):
    code = "POLICY_DENIED"


class PathEscape(WorldError):
    code = "PATH_ESCAPE"
