# Waza database — durable reality

## Active migration chain

```
001_waza_baseline
002_memory_graph          (historical; episodes later dropped)
003_retire_educational_tables
004_drop_tool_executions
005_drop_goals
006_drop_memory_graph
007_domain_reality_cleanup   ← head
```

## What belongs in the schema

| Area | Tables |
|------|--------|
| Identity & channels | `principals`, `interface_identities`, `channel_link_challenges` |
| Conversation | `conversations`, `messages`, `inbound_events` |
| World | `worlds` |
| Durable memory | `memories` (AI-owned; **goals** are rows with a goal-like `memory_type`, not a Goal engine) |
| Files | `artifacts` |
| Time | `scheduled_actions` |
| Work | `works`, `executions`, `deliveries` |
| Chat interactions | `interactions` |
| Temporary web workspace | `surfaces`, `surface_revisions`, `surface_sessions`, `surface_events`, `surface_ai_requests` |
| Rate protection | `principal_workloads` |

## What does **not** belong

- Goal / curriculum / quiz / mastery / assessment engines  
- Memory episode graphs or embedding columns as a second brain  
- Check-in product accounting  
- Tool execution registries  

## Clean rebuild (intentional data wipe)

1. Stop Worker (and Web if needed).
2. `DROP SCHEMA public CASCADE; CREATE SCHEMA public; …`
3. `alembic upgrade head`
4. Confirm `alembic_version` = `007`
5. Start services.

## Principals

`display_name` only — never `principals.name`.
