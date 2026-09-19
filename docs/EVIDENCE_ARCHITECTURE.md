# Evidence architecture

## Hierarchy
1. **Event** — something happened (messages, work) — always durable
2. **Observation** — learning-relevant signal — selective
3. **Evidence** — observable basis for a claim (task, answer, assistance) — when meaningful
4. **Hypothesis** — claim about the learner — candidate → active → confirmed
5. **Durable memory / learner state** — only when justified by evidence

## Rules
- Evidence is not an AI-invented score
- Assistance level changes evidence strength
- Hypotheses are never facts until confirmed
- Confirmed hypotheses may promote to Memory with provenance
- `why_we_believe(claim_key)` explains WHAT + WHY + WHEN + HOW WE KNOW

## Evidence types
explicit, performance, explanation, demonstration, correction, repetition,
persistence, transfer, self_report, tutor_intervention, independent_success, behavioral
