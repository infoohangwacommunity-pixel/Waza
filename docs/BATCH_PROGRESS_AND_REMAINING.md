# WAX infrastructure batch progress & remaining work

## Completed

| Batch | Commit | Scope |
|-------|--------|--------|
| P0 | 9aa1e8e | Interactions, TG callbacks, expiry, clock |
| P1 | fa2b312 | AgentRuntime, learner state, schedule_at, pause/resume |
| P1-B | 22d30cd | WA interactions, storage defaults, tool meta, research |
| P1-C | cc9a7d0 | Assessment timeout, workspace env, locks, quiet hours |
| P1-D | 95c1177 | Schedule cancel/series, leases, redeliver, search_web |
| **P2-A** | **this** | Privacy export, identity link, policy gate, HTML pages, health/detail |

## What “everything remaining” still means

Not shipped as production-complete systems yet (need dedicated design + time):

- Full ephemeral **web app** hosting (beyond static HTML artifact)
- Multi-tenant **admin console**
- Longitudinal **eval / chaos lab**
- Browser automation sandbox
- Full **GDPR workflow** UI (export exists; deletion workflow partial)
- Redis / multi-region
- Investor-grade telemetry dashboards

**High-value core OS path is largely in place:** durable work, interactions, schedule, agent runtime, channels, artifacts, research fetch, policy, export, leases.

Continue only for the long-tail items above — not random features.
