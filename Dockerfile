# Coherent production image: Python is guaranteed; media tools match sandbox allowlist.
# Avoids Nixpacks "python: command not found" when custom nixPkgs displace the provider.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        tesseract-ocr \
        tesseract-ocr-eng \
        sox \
        mediainfo \
        bubblewrap \
        poppler-utils \
        imagemagick \
        file \
        unzip \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && if [ ! -e /usr/local/bin/python ]; then ln -sf /usr/local/bin/python3 /usr/local/bin/python; fi

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Bake Vosk into the IMAGE at /opt/wax-models (NOT under /data).
# Railway volume mounts on /data hide any files baked under /data at build time.
# Runtime seed_models.sh copies into the volume once if empty.
RUN chmod +x scripts/provision_vosk_model.sh scripts/seed_models.sh \
    && scripts/provision_vosk_model.sh /opt/wax-models \
    && test -d /opt/wax-models/models/vosk \
    && find /opt/wax-models/models/vosk -maxdepth 1 -type d -name 'vosk-model-*' | grep -q .

# Volume path for durable workspaces/models; image path is the guaranteed source.
ENV WAX_MODEL_ROOT=/data/wax-models \
    WAX_IMAGE_MODEL_ROOT=/opt/wax-models \
    WAX_VOSK_ALLOW_DOWNLOAD=0 \
    WAX_AUTO_TRANSCRIBE=0 \
    WAX_VOSK_MODEL_NAME=vosk-model-small-en-us-0.15

# Default web start; Railway/worker override via startCommand / Procfile.
CMD ["bash", "scripts/start_web.sh"]
