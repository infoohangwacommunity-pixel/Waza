# Build fix — python: command not found (2026-09-21)

## Symptom

Railway build failed at:

```
RUN ... python -m pip install --upgrade pip
/bin/bash: line 1: python: command not found
exit 127
```

Both web and worker FAILED at BUILD (not migrate, not Volume, not Telegram).

## Root cause

Phase 2 `nixpacks.toml` overrode `[phases.install]` with:

```toml
cmds = [
  "python -m pip install --upgrade pip",
  "pip install -r requirements.txt",
]
```

That replaced Nixpacks' Python provider install step. In the generated image stage, the `python` binary is not on PATH (often only `python3` exists after provider setup, or the provider never runs correctly when install is fully custom).

## Fix (minimal)

1. Remove custom `[phases.install]` from `nixpacks.toml`.
2. Keep only `[phases.setup] nixPkgs = [...]` for media/isolation binaries.
3. Set `NIXPACKS_PYTHON_VERSION = "3.12"` so the Python provider is explicit.
4. Harden `scripts/startup.sh` to prefer `python3` and shim `python` → `python3` when needed.
5. Worker Procfile uses `python3 -m wax.workers.main`.

Do not change Alembic, Volume, workspace paths, or messaging for this failure.

## After deploy

Confirm build succeeds, then check logs for:

- `WAX DATABASE MIGRATION START`
- `WAX CAPABILITY PROBE`
