# Architecture

Infrastructure provides reality. AI provides intelligence.

## Flow

1. A channel webhook authenticates the message and creates durable Work.
2. A worker claims the Work.
3. Any inbound file is stored in the student’s World (path + size + type only).
4. The tutor sends the objective and recent conversation to the model.
5. The model may emit fenced directives. Infrastructure runs them and returns observations.
6. The model continues until it has a reply for the student.
7. Delivery sends the reply on the original channel.

## Pieces

**Identity** — A `Principal` is the person. Channel accounts (`InterfaceIdentity`) attach to that person. One person can use more than one channel without becoming two students.

**Work** — Every student turn is durable Work with status, attempts, and payload. Crashes and restarts resume from the database, not from process memory.

**World** — Each principal has a persistent workspace (files, packages, terminal). Isolation (bubblewrap or docker) bounds CPU, memory, network, and filesystem. Resource limits protect the host; they are not teaching policy.

**Memory** — Durable rows the AI searches, creates, updates, supersedes, or forgets. Recent chat is for continuity. The application does not auto-select or inject long-term memory.

**Scheduler** — The AI sets a delay or absolute time. Infrastructure stores the action and later creates Work. Finite series are capped for database safety, not to limit reasoning.

**Surfaces** — Temporary AI-authored web pages. Infrastructure hosts them with opaque tokens, CSP, and principal isolation. Design and content belong to the AI.

**Messaging** — WhatsApp and Telegram adapters normalize inbound events and deliver outbound text (and channel-native interactions). Presentation stays channel-aware; interpretation stays with the AI.

## Directives

Minimal bridge from model text to infrastructure:

| Channel | Meaning |
|---------|---------|
| `world` | Run a command inside the student’s World |
| `state` | Search / get / create / update / supersede / forget memory |
| `time` | Schedule, list, or cancel future Work |
| `publish` | Create or update a temporary surface |
| `interact` | Present choices on the current channel |

## Data

Schema source: `wax/db/models.py`. One baseline migration: `001_reality`. Tables cover principals, identities, conversations, messages, worlds, memories, artifacts, schedules, work/execution/delivery, interactions, surfaces, and rate-protection state.
