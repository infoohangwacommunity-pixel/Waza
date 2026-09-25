# Context Intelligence

## Idea

Stop treating context as only mechanical assembly.

**Context Intelligence** investigates *what the tutor needs to know for this turn*,
using the existing memory / evidence / learner-state / identity systems as tools.

```
Learner message
  → Context Intelligence (investigate)
  → domain capabilities (principal-scoped)
  → structured Context Brief
  → ContextResolver / Tutor
```

The model is **not** the database. Facts stay in Waza services.

## Modes

| Mode | Behavior |
|------|----------|
| `CONTEXT_INTELLIGENCE_ENABLED=true` + model | Tool-using investigation via provider abstraction |
| Model failure / disabled model | Bounded deterministic probe over the same capabilities |
| `CONTEXT_INTELLIGENCE_ENABLED=false` | Skip; existing gather_evidence path only |

## Capabilities (tools)

- `inspect_learner_state`
- `search_memories`
- `inspect_recent_conversation`
- `inspect_goals`
- `inspect_preferences`
- `search_evidence`
- `inspect_linked_channels`
- `inspect_hypotheses`

All enforce the authenticated `principal_id`. No raw SQL. No cross-learner access.

## Context Brief

Structured package: request understanding, task intent, facts/evidence vs inferences,
uncertainties, optional “no context required” / “insufficient evidence”.

Rendered into the tutor system prefix. Inferences must not be written as durable facts
from this block alone.

## Configuration

```
CONTEXT_INTELLIGENCE_ENABLED=true
CONTEXT_INTELLIGENCE_USE_MODEL=true
CONTEXT_INTELLIGENCE_MAX_TOOL_CALLS=6
CONTEXT_INTELLIGENCE_MAX_TOKENS=900
CONTEXT_INTELLIGENCE_TEMPERATURE=0.2
```

Uses the existing primary/fallback intelligence provider — no hardcoded model names.

## Non-goals

- Not a second student-facing personality
- Not a keyword/subject router
- Does not replace embeddings (still used inside memory retrieval)
- Does not replace EvidencePlanner / MemoryService
