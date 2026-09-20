# WAX infrastructure batch progress & remaining work

## Completed batches

| Batch | Commit | Scope |
|-------|--------|--------|
| P0 | 9aa1e8e | Interactions, TG callbacks, expiry, clock |
| P1 | fa2b312 | AgentRuntime, learner state, schedule_at, pause/resume |
| P1-B | 22d30cd | WA interactions, storage defaults, tool meta, research fetch |
| P1-C | cc9a7d0 | Assessment timeout, workspace env, row locks, quiet hours |
| **P1-D** | **this** | Schedule cancel/series, work lease renew/reclaim, artifact redeliver, search_web hook, race tests |

## Remaining high-value (approx)

Still open ~**250–300** conceptual items; ~**100–150** real eng units.

**~2–4 more large batches** for:
- Ephemeral websites / brand shell
- Full policy engine
- Identity linking / export
- Heartbeat-rich execution steps UI
- Chaos + longitudinal eval suites
- Browser/network capability beyond URL fetch

## Connected pipeline (current)

Telegram/WhatsApp → inbound → Interaction (optional) → Work → claim+lease →
AgentRuntime → tools (schedule/domain/research/artifacts) → Delivery →
recovery (expire interactions, reclaim stale leases, scheduler due)
