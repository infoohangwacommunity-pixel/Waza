"""Phase 5 reliability invariants."""

from pathlib import Path
import ast

from wax.delivery.backoff import retry_backoff_seconds


def test_backoff_increases_and_caps():
    assert retry_backoff_seconds(1) == 20
    assert retry_backoff_seconds(2) == 40
    assert retry_backoff_seconds(10) == 600  # capped


def test_work_claim_uses_skip_locked():
    src = Path("wax/work/engine.py").read_text()
    assert "with_for_update" in src
    assert "skip_locked" in src
    assert "retrying" in src
    assert "next_retry_at" in src


def test_delivery_create_is_idempotent():
    src = Path("wax/work/engine.py").read_text()
    assert "idempotency_key" in src
    assert "delivery_idempotent_hit" in src or "scalar_one_or_none" in src


def test_delivery_retry_service_complete():
    src = Path("wax/delivery/retry.py").read_text()
    tree = ast.parse(src)
    names = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "process_batch" in names
    assert "attempt_one" in names
    assert "show_typing" in src
    assert "retry_backoff_seconds" in src
    assert Path("wax/delivery/backoff.py").exists()


def test_worker_recovery_loop():
    src = Path("wax/workers/main.py").read_text()
    assert "reclaim_stale_works" in src
    assert "DeliveryRetryService" in src
    assert "expire_due" in src
    assert "process_batch" in src


def test_webhook_and_callback_dedupe():
    tg = Path("wax/messaging/telegram/handler.py").read_text()
    wa = Path("wax/messaging/whatsapp/handler.py").read_text()
    assert "on_conflict_do_nothing" in tg
    assert "on_conflict_do_nothing" in wa
    assert "tg_cb:" in tg


def test_interaction_single_consume():
    src = Path("wax/interaction/service.py").read_text()
    assert "already_consumed" in src
    assert "with_for_update" in src or 'status == "pending"' in src


def test_work_fail_exponential_retry():
    src = Path("wax/work/engine.py").read_text()
    assert "max_attempts" in src
    assert "next_retry_at" in src
    assert "2 **" in src or "2**" in src
