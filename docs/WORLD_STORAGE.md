# Student World storage (Railway)

## Constraint

Railway does **not** allow the same Volume to be mounted on Web and Worker.
Same mount path on two services means **two independent disks**, not one shared store.

## Architecture

```
Postgres       -> identity, Work, messages, memory, schedules (network-shared)
Worker Volume  -> Student Worlds filesystem (authoritative for World files)
Web            -> webhooks, surfaces, downloads (no World FS required for chat)
Artifacts      -> prefer S3/R2 when Web must serve files Worker wrote
```

**Worker** is the only process that must create, execute, stage media, and archive
transcript into `{WORKSPACE_ROOT}/worlds/{world_id}/`.

## Required Worker variables

```
APP_ENV=production
WORKSPACE_ROOT=/data/wax-workspaces
REQUIRE_PERSISTENT_WORKSPACE=true
```

Attach a Railway Volume to the **Worker** service with mount path `/data`.

## Web

Web does **not** need WORKSPACE_ROOT for WhatsApp/Telegram.
Keep existing web-volume if it holds other data; do not treat it as the Student World store.

For downloads of artifacts produced by Worker, use S3-compatible storage both can reach.

## Forbidden

- Two independent volumes both pretending to be the same World
- Production WORKSPACE_ROOT under /tmp
- Silent fallback to /tmp when REQUIRE_PERSISTENT_WORKSPACE=true

## Migration

1. Attach a Volume on **Worker** at `/data` (do not delete web-volume yet).
2. Set Worker env as above; redeploy Worker.
3. Confirm logs: no workspace_root_ephemeral; path is /data/wax-workspaces.
4. Optional one-time copy of any World trees from Web volume into Worker volume.
5. Only after verification, decide whether Web still needs its volume.

## Verification

- Worker restart: files under /data/wax-workspaces/worlds/ remain
- Deploy: same
- Web health may report world_store=unavailable_on_this_service (expected)
- Worker: WORKSPACE_ROOT durable and writable
