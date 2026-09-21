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

# Default web start; Railway/worker override via startCommand / Procfile.
CMD ["bash", "scripts/start_web.sh"]
