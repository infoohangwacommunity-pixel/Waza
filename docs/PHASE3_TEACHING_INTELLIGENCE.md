# Phase 3 — Teaching Intelligence (2026-09-21)

## Goal

Make the tutor behave like an adaptive teacher that uses evidence, not a chatbot with a quiz rhythm.

## Changes

### System prompt (`TUTOR_SYSTEM`)
- Explicit **teaching judgment** principles (not a fixed lesson script)
- Retrieval-first when learner forgets recently demonstrated knowledge
- Misconception handling: distinguish, contrast, targeted check
- Varied assessment forms (not only recall / Question 1–2–3)
- Evidence tools when durable learning signals appear
- Transfer checks when appropriate
- Permission to do less (one sentence / one question / pause)
- Tool honesty: never claim success without tool result
- Durable preferences: call `set_preference`; honor unless current turn overrides

### Context assembly
- `_preferences_block` surfaces durable prefs every turn so the model does not drift

### Preferences domain
- Added `emoji`, `tone`, `learning_style` defaults
- `set_preference` tool allowlist includes those keys

## Explicit non-goals
- No subject/exam mode branches
- No hardcoded curriculum sequence
- No QuizMode

## Tests
- `tests/test_teaching_intelligence.py`
