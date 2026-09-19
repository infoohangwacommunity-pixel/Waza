# Blueprint alignment (living)

Checked against the full WAX Prep blueprint. Memory is priority.

## Present and strong
- Person-first (Principal), not Student table as center
- Layered memory: extract, hybrid retrieve, embeddings, confidence, evidence, expire, supersede, forget
- Memory consolidation (async), isolation on failure
- Long-session continuity: rolling summary + episodic digests
- Context assembler (not full history dump)
- Tutor + memory roles (primary vs smaller model path)
- WhatsApp/Telegram adapters only own transport
- Fast webhook accept → durable Work → worker
- Delivery retries separate from tutoring success
- Scheduler tutor-decided (not 7pm student rule)
- Terminal as hands + AI workspace (media local-first)
- Goals lifecycle, artifacts generic
- Provider primary/fallback
- No cost gate on teaching

## Added in this pass (blueprint gaps)
- Memory validity_status, last_confirmed_at, last_observed_at, contradiction_of_id
- Concept + ConceptRelation + LearnerConceptState (personalized knowledge graph)
- Activity durable states (assessment/practice/session — not QuizMode)
- Observation table + pipeline (observations ≠ auto-memories)
- Tools: start/complete activity, update_concept_state
- Context includes concept understanding snapshot

## Still ahead (build order, not ignored)
- Richer timed activity interaction loop (question-by-question durable)
- Knowledge source ingestion (PDFs as artifacts + retrieval)
- Stronger evidence weighting rules in consolidation
- Synthetic learner journey tests
- Voice channel later
- Object storage for large blobs at scale
