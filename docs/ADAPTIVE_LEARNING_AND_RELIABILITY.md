# Adaptive learning signals, feedback, recovery, rate protection

## Philosophy

Infrastructure persists and protects. Intelligence interprets and decides.
A keyword is never student evidence. Attribution belongs to the tutor.

## Natural conversational signals

Post-turn path records **Observation** only.
Semantic interpretation uses existing:
- Memory extraction
- EvidencePlanner
- Tutor tools: `record_evidence`, `set_preference`, `form_hypothesis`

Tutor system prompt includes attribution rules (third-person, questions, current difficulty vs style).

## Web thumbs

`POST /s/{token}/api/events` with `type=response_feedback` and `payload.direction` + `response_ref`.
Principal from surface capability ownership (never browser-supplied principal_id).
Creates moderate-weight interaction Evidence via `record_explicit_interaction_evidence`.
Not an automatic durable preference.

## Check-ins

`wax/learner/checkin.py`: infrastructure gates (history, cooldown, weekly cap, suppress on active problem).
ContextAssembler surfaces eligibility; AI decides if the moment is natural.
No surveys, no forced ratings.

## Rate protection

`wax/protection/rate.py`: burst-tolerant token bucket after durable accept.
Never discards accepted messages.
Process-local state is an optimization; multi-worker hard shared enforcement needs a durable store (documented).

## Recovery

- Work reclaim / orphan Work (existing)
- `recover_orphan_messages`: Message without Work → recovery Work
- Recovery batching: sibling inbound messages in window as ordered context; originals preserved
- Safe `outage_context` for tutor (no secrets/internals)

## Safe errors

`wax/security/safe_errors.py`: student-facing messages never contain secrets or stack traces.
Permanent tutor failure queues one idempotent safe apology Delivery.
