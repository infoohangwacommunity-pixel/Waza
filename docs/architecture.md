# WAX Prep Architecture

## Core rule

Infrastructure provides reality. AI interprets and decides.

There is no tool registry, no primitive catalogue exposed to the model,
and no second "intelligence layer" that selects context or workflows for the AI.

## Request lifecycle

1. Channel webhook validates and persists inbound message + Work
2. Worker claims Work
3. Media (if any) is placed into the student's World — not interpreted
4. Tutor agent receives principal, history, memory snapshot, World snapshot
5. AI reasons; may emit free-form directive blocks
6. Infrastructure executes directives (World exec, memory writes, schedule, publish)
7. Observations return to the AI; it continues or finishes
8. Infrastructure delivers the reply

## Components

| Layer | Responsibility |
|-------|----------------|
| Messaging | Receive/send |
| Work / Worker | Durable claim, retry, recovery |
| Tutor agent | Brain — reason and act |
| World | Isolated persistent files/packages/terminal |
| Memory store | Durable state the AI owns (create/update/supersede/forget) |
| Scheduler | Store wake times; create Work when due |
| Security | Isolation, auth, secrets, sandbox |
| Delivery | Channel send + retries |
| Surfaces | Secure temporary web exposure of AI-authored pages |

## Explicitly absent

- Tool registry / ToolSpec / function-calling tool menus
- Primitive catalogue presented to the model as tools
- Context Intelligence / Context Assembler
- Learner / assessment / knowledge engines
- Automatic media interpretation pipelines
- Artificial intelligence step budgets (only infrastructure safety ceilings)

## Directives (not tools)

The model may include fenced blocks in its text:

- `world` — run commands/scripts in the student World
- `memory` — search/create/update/supersede/forget durable state
- `schedule` — ask infrastructure to wake later
- `publish` — create a temporary web surface
- `choices` — present interactive choices

These are parsed from free text. They are not OpenAI tool schemas.

## Database

Core durable state: principals, identities, worlds, conversations, messages,
works, executions, deliveries, memories, goals, artifacts, scheduled_actions,
interactions, surfaces, channel_link_challenges.

Educational/intelligence explosion tables are retired.


## Intelligence vs infrastructure safety

Application code must **not** impose artificial intelligence limits such as:
- max tool rounds / max actions as a teaching or reasoning budget
- forced max_tokens ceilings on tutor completions
- memory or context packages that cap what the model may know

Infrastructure **may** enforce safety boundaries against:
- runaway agent loops (continuation ceiling, wall clock)
- resource exhaustion (process CPU/memory, output size, rate limits)
- security violations (sandbox, principal isolation)

Those safety boundaries are not conceptual limits on what the AI is allowed to understand or decide.


## Infrastructure channels (directive bridge)

Fenced blocks are a **minimal machine-readable bridge**, not a tool catalogue.

| Channel | Infrastructure domain |
|---------|----------------------|
| `world` | General execution environment |
| `state` | Durable student persistence |
| `time` | Delayed / scheduled wake |
| `publish` | Secure temporary publication |
| `interact` | Channel interaction (choices) |

Aliases: `memory`→`state`, `schedule`→`time`, `choices`→`interact`.

The AI decides the objective. Infrastructure validates security and executes.
No specialized teaching actions. No application capability menu sent to the model.


## World persistence

Each principal has an isolated World under `WORKSPACE_ROOT/worlds/<world_id>/`.

**Durable (must survive restart/deploy on a volume):**
`workspace/`, `projects/`, `software/`, `runtimes/`, `history/`, identity/lifecycle metadata.

**Temporary (may be age-cleaned):**
`tmp/`, `cache/` only — controlled by `WORKSPACE_TMP_TTL_HOURS`.

There is **no** automatic expiration of a student's World after 72 hours.
Isolation is by distinct world roots; paths cannot escape the world root.


## Surface workspaces

Surfaces are **temporary AI-authored web workspaces**, not a messaging channel.

- AI writes HTML/CSS/JS (design, color, cartoons, lists, interaction UI).
- AI chooses lifetime (hours / week / etc.) and may update or revoke.
- Infrastructure: opaque tokens, CSP, principal isolation, expire/cleanup, optional state/events API for the page.

No application-forced brand theme. No surface intelligence engine deciding when a student "needs a website."
