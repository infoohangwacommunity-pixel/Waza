# Cross-channel identity continuity

## Model

- **Principal** = one learner
- **InterfaceIdentity** = WhatsApp / Telegram door to that learner
- After verified linking, both channels resolve to the same Principal
- Memory, evidence, preferences, goals, mastery belong to the Principal — never copied per channel

## Authoritative state for the tutor

`linked_channels_state(session, principal_id, current_channel)` builds semantic facts:

- current interface
- WhatsApp linked / not linked
- Telegram linked / not linked
- verified channel list

This is injected into:

- `ContextResolver` system prefix
- `ContextAssembler` identity block (degraded path)

The tutor must not invent linked status. The identities block is the source of truth.

## Linking flow

1. Learner naturally asks to use the other app
2. Tutor calls `request_channel_link` (OTP preferred)
3. Code delivered on target channel
4. Learner pastes code → `confirm_channel_link`
5. `InterfaceIdentity` attached to Principal
6. Next inbound on either channel resolves to same Principal via handler `_resolve_identity`

## Security

- Target identity owned by another Principal → `identity_conflict` (OTP not started / confirm refused)
- No AI-decided merges
- No keyword triggers for linking or onboarding

## Continuity vs transcript dump

Identity continuity is mandatory. Full chat dump across channels is not.
Relevant continuity uses Principal-scoped memory, evidence, goals, and conversation summaries — not automatic injection of every other-channel message.
