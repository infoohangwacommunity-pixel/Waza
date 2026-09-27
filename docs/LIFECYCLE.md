# Waza intelligence lifecycle

One circle (all channels):

1. **Ingest** — WhatsApp / Telegram / Web / Surface → durable `Work`
2. **Claim** — Worker lease + attempt
3. **Context Intelligence** — `inspect_continuity_digest` first → brief + capability families
4. **Tutor** — only selected tools; bounded rounds
5. **Delivery** — channel render + paced multi-chunk
6. **Telemetry** — `turn.completed` (tokens, calls, path)
7. **Memory** — `memory_process` (skipped if cool-down or too short)
8. **Digest refresh** — structured rebuild for next turn
9. **Research** — hypothesis suggestions when providers are cool
10. **Recovery** — 429 → `retrying` + resuscitate; never silent drop

Rate limits: pure 429s do not burn `max_attempts`. Background consolidation/research pause while any provider is cooling down.
