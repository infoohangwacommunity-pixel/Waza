# Infrastructure batch progress

## Done (P0) — commit 9aa1e8e
- Interaction engine, Telegram callbacks, expiry, clock tool, agent_max_tool_rounds

## Done (P1 batch) — this commit
- AgentRuntime: Execution checkpoints + tool step log + resume
- Learner state snapshot in context (goals, activities, pending interactions, schedule)
- schedule_at (absolute time + timezone metadata)
- Domain tools: get_learner_state, pause_activity, resume_activity, set_preference, schedule_at
- Activity pause/resume state machine transitions
- Work claim lease_until metadata
- Preferences: timezone, proactivity_level

## Next batch candidates
- Object storage default in production
- Research capability (search/fetch/cite)
- Richer assessment item progression on interaction timeout
- WhatsApp interactive consume path
- Tool risk/permission metadata
- Chaos tests for interaction races
