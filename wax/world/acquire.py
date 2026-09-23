"""Generic software acquisition — transactional, verified, world-local."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from wax.observability.logging import get_logger
from wax.world.errors import AcquisitionFailed, AcquisitionUnavailable, VerificationFailed
from wax.world.manager import World, assert_operable
from wax.world.providers.base import AcquireRequest
from wax.world.providers.pip import PipProvider
from wax.world.resources import check_world_disk

logger = get_logger(__name__)

_PROVIDERS = [PipProvider()]


def register_provider(p) -> None:
    _PROVIDERS.append(p)


async def acquire(world: World, req: AcquireRequest) -> dict[str, Any]:
    assert_operable(world)
    check_world_disk(world.root)
    req.world_id = world.world_id

    provider = next((p for p in _PROVIDERS if p.can_handle(req)), None)
    if provider is None:
        raise AcquisitionUnavailable(
            f"no provider for kind={req.kind}",
            detail="v1 supports python_package via pip; other kinds are extensible",
        )

    lock = world.root / "state" / "locks" / f"acquire-{req.kind}-{req.name}.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    if lock.exists():
        age = time.time() - lock.stat().st_mtime
        if age < 600:
            return {"ok": False, "error": "acquire_locked", "detail": "another install in progress"}
    lock.write_text(str(time.time()), encoding="utf-8")
    txn_id = uuid.uuid4().hex[:12]
    txn_path = world.root / "state" / "installs" / f"{txn_id}.json"
    txn = {"id": txn_id, "req": {"kind": req.kind, "name": req.name}, "status": "running", "started": time.time()}
    txn_path.write_text(json.dumps(txn), encoding="utf-8")

    try:
        plan = await provider.plan(req)
        result = await provider.install(req, world.root, plan)
        if not result.ok:
            txn["status"] = "failed"
            txn["error"] = result.error
            txn_path.write_text(json.dumps(txn), encoding="utf-8")
            if result.error == "verification_failed":
                raise VerificationFailed(result.detail or "verify failed")
            raise AcquisitionFailed(result.error or "acquire failed", detail=result.detail)
        # record observed software
        soft = world.root / "software" / f"{req.kind}-{req.name}.json"
        soft.write_text(json.dumps(result.observed, indent=2), encoding="utf-8")
        txn["status"] = "ok"
        txn["observed"] = result.observed
        txn_path.write_text(json.dumps(txn), encoding="utf-8")
        logger.info(
            "world_acquire_completed",
            world_id=world.world_id,
            name=req.name,
            kind=req.kind,
        )
        return {"ok": True, "observed": result.observed, "transaction_id": txn_id}
    finally:
        try:
            lock.unlink(missing_ok=True)
        except Exception:
            pass
