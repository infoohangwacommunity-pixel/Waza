# Railway deployment contract

This is the exact operator guide. Variable names match `.env.example` and runtime settings.

Do **not** deploy from this document automatically — configure Railway yourself.

## Services

Use **one image** built from the repository `Dockerfile` (`railway.toml` → `builder = DOCKERFILE`).

| Process | Railway start command | Role |
|---------|----------------------|------|
| **Web** | `bash scripts/start_web.sh` | HTTP API, webhooks, Surfaces, health |
| **Worker** | `bash scripts/startup.sh python3 -m wax.workers.main` | Claims Work, runs tutor, delivery, scheduler |

Both commands run migrations + schema verify before starting (`scripts/startup.sh`).

Railway injects `PORT` for the web process. Do **not** put a literal `$PORT` in `startCommand` (the script reads `PORT` from the environment).

Share the **same variables** and the **same volume** across web and worker.

## Volume (student Worlds)

1. Create a Railway Volume.
2. Mount it at **`/data`** on **both** web and worker.
3. Set:

```text
WORKSPACE_ROOT=/data/wax-workspaces
REQUIRE_PERSISTENT_WORKSPACE=true
```

Production **rejects** a workspace under `/tmp`. Student Worlds are durable; they must not live on ephemeral container disk.

Optional artifact files on the same volume:

```text
WAX_ARTIFACT_ROOT=/data/wax-artifacts
STORAGE_BACKEND=local
```

## Database (Railway Postgres)

1. Add a Postgres plugin.
2. Set:

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
```

(Use your plugin’s reference syntax if the service name differs.)

The app accepts `postgres://` / `postgresql://` and normalizes to `postgresql+asyncpg://`.

## REQUIRED variables

| Variable | Purpose |
|----------|---------|
| `APP_ENV` | Must be `production` in production |
| `SECRET_KEY` | Strong random secret (not a placeholder) |
| `PUBLIC_BASE_URL` | HTTPS origin of the **web** service (no trailing slash) |
| `DATABASE_URL` | From Railway Postgres |
| `PRIMARY_PROVIDER` | e.g. `openai` |
| `PRIMARY_API_KEY` | Provider API key |
| `PRIMARY_MODEL` | e.g. `gpt-4o-mini` |
| `WORKSPACE_ROOT` | `/data/wax-workspaces` |
| `REQUIRE_PERSISTENT_WORKSPACE` | `true` |

### Channels (enable what you use)

**WhatsApp**

| Variable | Purpose |
|----------|---------|
| `WHATSAPP_ENABLED` | `true` |
| `WHATSAPP_ACCESS_TOKEN` | Cloud API token |
| `WHATSAPP_PHONE_NUMBER_ID` | Phone number id |
| `WHATSAPP_APP_SECRET` | App secret (signature verify) |
| `WHATSAPP_VERIFY_TOKEN` | Webhook verify token you choose |

Webhook URL: `{PUBLIC_BASE_URL}/webhooks/whatsapp`

**Telegram**

| Variable | Purpose |
|----------|---------|
| `TELEGRAM_ENABLED` | `true` |
| `TELEGRAM_BOT_TOKEN` | Bot token |
| `TELEGRAM_WEBHOOK_SECRET` | Secret you choose for webhook |

Webhook URL: `{PUBLIC_BASE_URL}/webhooks/telegram`  
Set webhook: `https://api.telegram.org/bot<TOKEN>/setWebhook?url=<PUBLIC_BASE_URL>/webhooks/telegram&secret_token=<TELEGRAM_WEBHOOK_SECRET>`

## OPTIONAL variables

Everything else in `.env.example` under **OPTIONAL** has safe defaults: fallback provider, pool sizes, rate limits, isolation numbers, S3 artifact backend, `SURFACE_PUBLIC_ORIGIN`, etc.

Only set them when you need non-default behaviour.

## LOCAL-ONLY

For laptop development only (see bottom of `.env.example`):

- `APP_ENV=development`
- local Postgres URL
- `WORKSPACE_ROOT=/tmp/wax-workspaces` with `REQUIRE_PERSISTENT_WORKSPACE=false`

Do not use those values on Railway.

## Health

- Web health path: `/health` (configured in `railway.toml`)
- Ready check: `/ready`

## Checklist

1. Dockerfile build succeeds  
2. Postgres plugin → `DATABASE_URL`  
3. Volume mounted at `/data` on web **and** worker  
4. `WORKSPACE_ROOT=/data/wax-workspaces`  
5. `PUBLIC_BASE_URL` = public web URL  
6. `PRIMARY_API_KEY` + model  
7. Web start: `bash scripts/start_web.sh`  
8. Worker start: `bash scripts/startup.sh python3 -m wax.workers.main`  
9. Point WhatsApp/Telegram webhooks at `PUBLIC_BASE_URL`  
