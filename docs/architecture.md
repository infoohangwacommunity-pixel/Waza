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
