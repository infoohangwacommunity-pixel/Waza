# Build strategy + local media (no paid transcription API)

## Railway build

**Builder: Dockerfile** (`railway.toml`).

Why: Nixpacks was generating `python -m venv` / `python -m pip` while `python` was not on PATH after custom `nixPkgs` setup → exit 127.

Dockerfile uses `python:3.12-slim-bookworm` so `python` and `python3` both exist, and installs:

ffmpeg, tesseract, sox, mediainfo, bubblewrap, poppler-utils, imagemagick, file, unzip

Do not put API keys in Docker `ARG`/`ENV` build lines. Runtime env vars only.

### MEMORY_API_KEY

Optional separate provider for a cheaper memory model. **Not required** for:

- tutoring (PRIMARY_API_KEY)
- local audio transcription (Vosk)
- image OCR (tesseract)

If Railway warns that MEMORY_API_KEY appears in build ARG, remove it from Docker build args in the Railway UI; keep it as a **runtime** variable only if you use MEMORY_PROVIDER.

## Local audio / images (no GPT bill for media)

| Media | Local tool | Paid API |
|-------|------------|----------|
| Voice notes | ffmpeg → Vosk (offline) | Only if `WAX_TRANSCRIPTION_ALLOW_API=1` |
| Photos / pages | tesseract OCR via `inspect_media` | Optional `describe_image` vision |
| PDF text | pdftotext | — |

Vosk model downloads once into `$WAX_WORKSPACE_ROOT/models/vosk` (prefer `/data/...` volume).

## Operator checklist

1. Railway service builder = Dockerfile (from repo `railway.toml`)
2. Volume at `/data`, `WORKSPACE_ROOT=/data/wax-workspaces`
3. Redeploy web + worker from this commit
4. Confirm build log shows Dockerfile stages, not `python: command not found`
