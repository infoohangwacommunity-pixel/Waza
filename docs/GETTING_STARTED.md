# Getting started (non-expert friendly)

WAX Prep is software that talks to people on WhatsApp and Telegram and tutors them over time.

## Pieces you run

1. **Database** (PostgreSQL) — remembers people, messages, work, memory
2. **API process** — receives WhatsApp/Telegram webhooks
3. **Worker process** — thinks (AI), replies, updates memory

## What you must configure

- `DATABASE_URL`
- `PRIMARY_API_KEY` (OpenAI, Grok/xAI, OpenRouter, etc.)
- WhatsApp tokens **or** Telegram bot token (or both)

## What you do not need to invent

- Subject menus
- Exam modes
- Quiz engines

The tutor discovers what the person needs through conversation.

## Media (photos, audio, video)

When someone sends a photo:

1. WAX downloads it into a private workspace folder
2. The AI can OCR / probe it with local tools
3. Only if that is not enough, it can call a vision model

That saves money and keeps the terminal as the AI’s workbench.

## First real test

1. Start API + worker
2. Point WhatsApp or Telegram webhook at your API
3. Message: `Hi`
4. You should get a natural reply
5. Message again later — it should remember useful things
