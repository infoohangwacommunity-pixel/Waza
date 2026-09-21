# Phase 5 — Reliability (2026-09-21)

## Principle

Deterministic software guarantees reality. Intelligence interprets it.

## Verified / strengthened

| Concern | Mechanism |
|---------|-----------|
| Work claim | `FOR UPDATE SKIP LOCKED` |
| Work retry | status=`retrying`, exponential `next_retry_at` |
| Stale work | `reclaim_stale_works` + leases |
| Webhook dedupe | `InboundEvent` unique (channel, external_event_id) + ON CONFLICT DO NOTHING |
| Callback dedupe | `tg_cb:{callback_id}` external id |
| Interaction | consume once, `already_consumed` |
| Delivery | idempotency_key lookup before insert |
| Delivery retry | `DeliveryRetryService.process_batch` in worker recovery loop |
| Backoff | pure `retry_backoff_seconds` (capped) |
| Typing on retry | retry path passes `show_typing` + inbound id when known |

## Tests

`tests/test_reliability_phase5.py`
