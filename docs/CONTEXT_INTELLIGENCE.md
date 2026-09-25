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

## Configuration (independent of the Tutor)

Context Intelligence has its **own** provider namespace. It does **not** inherit the
main tutor model unless you explicitly set `CONTEXT_INTELLIGENCE_FALLBACK_TO_PRIMARY=true`.

```
CONTEXT_INTELLIGENCE_ENABLED=true
CONTEXT_INTELLIGENCE_USE_MODEL=true
CONTEXT_INTELLIGENCE_PROVIDER=openai   # or grok | openrouter | anthropic | none
CONTEXT_INTELLIGENCE_API_KEY=
CONTEXT_INTELLIGENCE_BASE_URL=
CONTEXT_INTELLIGENCE_MODEL=
CONTEXT_INTELLIGENCE_TIMEOUT_SECONDS=45
CONTEXT_INTELLIGENCE_MAX_RETRIES=1
CONTEXT_INTELLIGENCE_MAX_TOOL_CALLS=6
CONTEXT_INTELLIGENCE_MAX_TOKENS=900
CONTEXT_INTELLIGENCE_TEMPERATURE=0.2
CONTEXT_INTELLIGENCE_FALLBACK_TO_PRIMARY=false
```

- Tutor uses `PRIMARY_*` / `FALLBACK_*`
- Context Intelligence uses `CONTEXT_INTELLIGENCE_*`
- Same provider/model only if you configure both the same way on purpose
- If CI provider/key/model unset and fallback_to_primary is false → deterministic probe only

## Non-goals

- Not a second student-facing personality
- Not a keyword/subject router
- Does not replace embeddings (still used inside memory retrieval)
- Does not replace EvidencePlanner / MemoryService


## Orchestration modes

| Mode | Behavior |
|------|----------|
| `CONTEXT_INTELLIGENCE_MODE=investigate` | CI investigates + ContextBrief; Tutor (PRIMARY) responds |
| `CONTEXT_INTELLIGENCE_MODE=unified` | CI may set `response_mode=unified` + `direct_reply`; Tutor skips LLM loop |

## Evidence control

When `CONTEXT_INTELLIGENCE_CONTROLS_EVIDENCE=true` (default), full `gather_evidence()`
runs only if the brief sets `needs_evidence_gather=true`. Minimal turns can skip it.

## Expanded capabilities

Materials, knowledge graph, conversation summary, cross-channel continuity, plus
memory/evidence/state/goals/preferences/hypotheses/identity.
