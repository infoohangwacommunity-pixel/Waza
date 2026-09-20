# WAX Infrastructure Rebuild Specification

**Status:** Living architecture plan  
**Source of truth for product philosophy:** `docs/BLUEPRINT_ALIGNMENT.md`, `docs/architecture.md`  
**Rule:** Do **not** implement user examples as hardcoded features. Examples reveal missing **generic infrastructure**.

**Central invariant:**  
> Intelligence decides. Infrastructure provides capabilities. Software enforces reality.  
> State is durable. Actions are observable. Users are isolated. Channels are replaceable. Work is recoverable.

---

## 0. What this document is / is not

| This is | This is not |
|---------|-------------|
| Ordered rebuild plan (P0 → P2) | A request to implement 500 items at once |
| Gap map against **current** code | A full rewrite of the repo |
| File / table / service mapping | Subject/exam/quiz feature tickets |
| Test requirements per subsystem | Railway topology redesign for its own sake |

**Process for every subsystem:**  
audit → design → migration (if needed) → implement → tests → deploy → next subsystem.

---

## 1. Current foundation (keep and strengthen)

| Area | Location | Status |
|------|----------|--------|
| Core loop | `docs/architecture.md` | Correct direction |
| Webhooks pure | `wax/messaging/telegram/handler.py`, `whatsapp/handler.py` | Accept + queue Work |
| Work engine | `wax/db/models.py` (`Work`), `wax/workers/main.py` | Claim / retry / recovery |
| Tutor + tools | `wax/intelligence/tutor.py`, `wax/tools/registry.py` | Tool loop exists; **`max_rounds = 3`** is a ceiling |
| Activities | `wax/work/activities.py`, `Activity` model | `ends_at` stored; expiry is **poll-based** |
| Assessments | `wax/assessment/service.py`, assessment models | Duration + items; not full interaction engine |
| Scheduler | `wax/scheduler/service.py` | Relative hours; FK/idempotency improved |
| Memory / evidence | `wax/memory/*`, `docs/EVIDENCE_ARCHITECTURE.md` | Present; consolidation FK fixed |
| Artifacts | `wax/artifacts/*` | TXT/PDF + signed download; TG document partial |
| Terminal / workspace | `wax/terminal/*` | Per-principal paths; `/tmp` + TTL; allowlist sandbox |
| Channels | `wax/delivery/*`, messaging clients | Presentation ≠ durable interaction state |
| Migrations | Alembic **007** | Do not regress; new work = **008+** only when needed |

**Mental model shift**

| Today (narrow) | Target |
|----------------|--------|
| AI has a toolbox | AI has a **world** (state + capabilities + durable time) |
| Chatbot + tools | Learner intelligence + agent runtime + durable world |
| `ends_at` + poll | Deadline → event → transition → AI decides next |
| Buttons as markup | **Interaction** rows (server-authoritative) |
| `max_rounds = 3` | Execution **budget** + checkpoints |
| `/tmp` workspaces | Scratch vs **object storage** durable artifacts |

---

## 2. Explicit non-goals (do not build)

- `if subject == biology`, JAMB packs, 5-second-quiz product mode  
- `telegram_timer.py` / channel-owned product logic  
- Raw `DATABASE_URL` / host shell for the model  
- Curriculum databases or forced onboarding flows  
- Rewrite of Postgres, Kafka, K8s, 15 microservices “because advanced”  
- Implementing the full 500-item backlog in one pass  

**Legitimate hardcoding (keep):** API limits, timeouts, quotas, pool sizes, max file size, retry ceilings.

---

## 3. Priority ladder

### P0 — Make the world real (correctness + durable interaction/time)

Ship these **before** research browsers, websites, or scale toys.

| ID | Capability | Why | Primary touchpoints | Schema | Tests |
|----|------------|-----|---------------------|--------|-------|
| **P0.1** | **Interaction engine** | Buttons are presentation-only; no consume/expire/replay | New `wax/interaction/`; `wax/tools/registry.py` (`present_choices`); TG/WA handlers + clients; `wax/delivery/senders.py` | New `interactions` (008) | Double-tap, expire, wrong principal, replay |
| **P0.2** | **Deadline / expiry pipeline** | Poll marks `Activity` expired; does not invalidate interaction or advance | `wax/work/activities.py`, `wax/assessment/service.py`, `wax/workers/main.py` recovery, `wax/scheduler/service.py` | Optional `deadline_events` or schedule rows | Timeout vs answer race; only one worker advances |
| **P0.3** | **Server-authoritative time** | Model must not “know” the clock | New tool `get_current_time`; prefs timezone later | Principal prefs only | Deterministic UTC + optional TZ |
| **P0.4** | **Tool result honesty (software)** | Prompt-only “don’t claim sent” is weak | Tutor post-process; delivery outcomes; artifact status | — | Failed tool ⇒ no success claim in enforced path |
| **P0.5** | **Tenant checks audit** | Isolation must be structural | memory/artifact/goal tools; workspace paths | — | Cross-principal read denied |

**P0.1 `interactions` (conceptual columns)**  
`id`, `principal_id`, `activity_id?`, `work_id?`, `channel`, `external_message_id?`, `callback_token` (opaque, ≤64 for TG), `choices` JSONB, `status` (`pending|consumed|expired|cancelled`), `expires_at`, `consumed_at`, `consumed_choice_id?`, `metadata`

**Flow (generic, not “5s quiz”):**  
create interaction → deliver → on callback: lock row → validate principal/status/expiry → consume → enqueue Work → AI decides next.  
On deadline: expire interaction → optional channel markup clear → evidence/timeout event → enqueue Work for AI.

---

### P1 — Agent runtime + durable storage + safer tools

| ID | Capability | Why | Primary touchpoints | Notes |
|----|------------|-----|---------------------|-------|
| **P1.1** | **Agent execution budget** | Replace fixed `max_rounds = 3` | `wax/intelligence/tutor.py`, `Work`/`Execution` | Limits: time, tool calls, output size—not “3 forever” |
| **P1.2** | **Checkpoints** | Crash must not erase multi-step work | `wax/db/models.py` Execution; worker | Resume from last completed step |
| **P1.3** | **Object storage default in prod** | `/tmp` is not durable | `wax/artifacts/storage.py`, settings, Railway bucket | Workspace = scratch; artifacts = bucket |
| **P1.4** | **Capability-shaped tools** | Not 200-binary allowlist | `wax/terminal/*`, `wax/tools/registry.py` | Permissions, risk, timeout, tenant scope |
| **P1.5** | **Domain tools (no raw SQL)** | AI must not hold DB credentials | New thin tools → existing services | `get/update` goal, memory, activity, schedule, artifact |
| **P1.6** | **Scheduler: absolute + TZ** | Hours-only is incomplete | `wax/scheduler/service.py` | Store UTC + timezone metadata; app-level not Railway cron for learner events |
| **P1.7** | **Active task / interrupt stack** | Biology → market list | Learner state snapshot in context | Pause/resume without subject ifs |

---

### P2 — Research, rich artifacts, eval, scale

| ID | Capability | When |
|----|------------|------|
| **P2.1** | Research capability (search/fetch/cite; provenance; not auto-memory) | After P0–P1 |
| **P2.2** | Artifact kinds beyond TXT/PDF; optional ephemeral web shell (brand config, not AI inventing brand) | After durable storage |
| **P2.3** | Learner-state orchestration (evidence decay, misconception status, next_action **candidates**) | Ongoing |
| **P2.4** | Chaos + longitudinal + multi-tenant tests | Continuous |
| **P2.5** | Queue/replicas/Redis **only if metrics demand** | Not premature |

---

## 4. Gap matrix (blueprint symptom → infrastructure)

| Observed symptom | Missing primitive | Priority |
|------------------|-------------------|----------|
| 5s buttons don’t expire / advance | Interaction + deadline events | P0 |
| Biology → shopping list whiplash | Active task stack + context | P1 |
| “Install Python once” | Workspace env **manifest** (not full VM) | P1/P2 |
| AI “know the time” | `get_current_time` tool | P0 |
| PDF/link weak | Artifact + object storage + delivery ids | Partially done → finish P1.3 |
| Website idea | Ephemeral deploy capability | P2 |
| `max_rounds = 3` | Budgeted agent runtime | P1 |
| Memory/schedule FK races | Flush ordering (done); keep discipline | Guardrails |

---

## 5. Architecture invariants (enforce in code + CI over time)

1. Channel adapters **never** import/call `TutorService` for reasoning.  
2. All learner data access is **principal-scoped** in software.  
3. LLM output **cannot** bypass tool auth or claim failed side effects as success.  
4. Durable work **survives** process restart.  
5. Artifacts **must not** depend on workspace TTL paths.  
6. Interactive actions are **server-authoritative** (DB status, not visible keyboard alone).  
7. No subject/exam/curriculum **product** modules.

Expand `tests/test_webhook_purity.py` and forbidden-pattern CI accordingly.

---

## 6. Railway / ops (constraints, not features)

| Concern | Guidance |
|---------|----------|
| Durable files | S3-compatible **bucket** (e.g. Railway Buckets); not only container FS |
| Volumes | Avoid as sole strategy if replicas; DB + object storage for shared durable state |
| Learner schedules | **App scheduler** + DB; Railway cron only for ops (cleanup, health) |
| Config | `PUBLIC_BASE_URL`, storage env vars; no hardcoded public host in code |
| Topology | API + Worker (+ optional later split of recovery); Postgres; object storage |
| Migrations | Controlled upgrade (startup OK for single worker; lock if multi-replica later) |

---

## 7. Recommended execution order (developer)

1. **This week (P0.1–P0.2):** Interaction table + consume/expire API + wire `present_choices` + TG `callback_query` + deadline work items.  
2. **P0.3–P0.5:** Clock tool; harden tool/delivery honesty; tenant tests.  
3. **P1.1–P1.3:** Budgeted tool loop + checkpoints; production object storage.  
4. **P1.4–P1.7:** Domain tools; richer schedule; task stack in context.  
5. **P2:** Research, web artifacts, eval harness—only after P0 solid.

**Definition of done for P0:**  
A timed multi-choice activity of **any** duration (5s or 30m) can: present options → accept one answer XOR expire once → never double-apply → enqueue exactly one continuation Work → AI chooses next step—**without** a “five-second quiz” module.

---

## 8. Composition examples (not feature tickets)

| User intent | Composition |
|-------------|-------------|
| Short timed practice | Activity + Assessment items + Interaction + deadline + Evidence + Work |
| PDF | Workspace/generate + Artifact + storage + Delivery |
| Reminder | Clock + Scheduler + Work + Delivery |
| “Website” later | Workspace + Artifact(application) + deploy + expiry + Delivery |

---

## 9. Documentation follow-ups

| Doc | Purpose |
|-----|---------|
| `docs/PRODUCT_BLUEPRINT.md` | Full product north star in-repo |
| `docs/adr/ADR-00x.md` | Decisions (Postgres, no channel-owned IQ, no raw SQL to model, …) |
| This file | Rebuild backlog and priorities |

---

## 10. One-paragraph brief for implementers

Do not implement user examples as hardcoded product features. Treat them as evidence of missing generic infrastructure. Preserve open-world WAX: no curriculum modes. Strengthen durable learner-related state, an interaction/deadline engine, a budgeted agent runtime, capability-based tools, per-principal workspace bounds, object-stored artifacts, timezone-aware scheduling, and provenance-aware memory/evidence. The model decides; software enforces ownership, time, state transitions, delivery, and recovery. Prefer composable primitives over scenario-specific code paths.
