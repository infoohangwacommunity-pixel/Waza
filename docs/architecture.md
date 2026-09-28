# WAX Prep Architecture

## Core rule

Infrastructure provides reality. AI interprets and decides.

## Request lifecycle

1. Channel webhook validates and persists inbound message + Work
2. Worker claims Work
3. Media (if any) is placed into the student's World — not interpreted
4. Tutor (brain) receives principal, message, World access, primitives
5. AI decides: memory R/W, World exec, schedule, publish, reply
6. Infrastructure delivers and stores durable state the AI requested

## Components

| Layer | Responsibility |
|-------|----------------|
| Messaging | Receive/send, no tutoring logic |
| Work / Worker | Durable claim, retry, recovery |
| Tutor | Brain — decide and call primitives |
| Primitives | memory_*, world_*, schedule, publish, present_choices |
| World | Isolated persistent files/packages/terminal |
| Security | Isolation, auth, secrets, sandbox |
| Delivery | Channel send + retries |
| Interaction | Server-authoritative choices/expiry |

## Explicitly absent

- Context Intelligence (second brain)
- Automatic context assembler selection
- Specialized media intelligence pipelines
- Educational workflow tool menus
- Token-budget intelligence gates

## Database baseline

Core tables only:

- principals, interface_identities, worlds
- conversations, messages, inbound_events
- works, executions, tool_executions, deliveries
- memories, memory_episodes, memory_links
- goals, artifacts, scheduled_actions, interactions
- channel_link_challenges
- surfaces (+ revisions, sessions, events, ai_requests)
- principal_workloads

Educational/intelligence explosion tables (assessments, concepts, evidence graphs,
automatic observations, publications, etc.) are retired. Schema is reproducible
from Alembic revisions 001 → 002 → 003.

## Security boundary

AI has broad capability **inside** the boundary: World isolation, quotas,
secret protection, webhook verification, rate protection. Infrastructure
does not interpret student meaning or invent durable facts.
