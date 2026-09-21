# Phase 6 — Evaluation (2026-09-21)

## Goal

Evaluate WAX as a tutor across full conversation intents — not only unit endpoints.

## Components

- `wax/eval/scenarios.py` — scenarios A–O from the master upgrade prompt
- `wax/eval/rubric.py` — internal tutor quality criteria
- `wax/eval/capability_map.py` — maps required capabilities → repository evidence
- `tests/test_conversation_eval_suite.py` — fails if any scenario’s capabilities are missing in code

## What this is / is not

**Is:** architecture coverage for conversation journeys (audio, ingest, memory, buttons, honesty…).

**Is not:** automated LLM grading of live tutoring quality (that needs production transcripts + human/LLM judges later).

## Extending

1. Add a `Scenario` with `required_capabilities`
2. Ensure `capability_evidence()` can detect the capability
3. Suite will enforce it in CI
