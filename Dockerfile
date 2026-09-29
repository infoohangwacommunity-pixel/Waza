# Authoritative production image (Railway builder = DOCKERFILE).
# Only packages required for runtime + World isolation.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        bubblewrap \
        ca-certificates \
        bash \
    && rm -rf /var/lib/apt/lists/* \
    && if [ ! -e /usr/local/bin/python ]; then ln -sf /usr/local/bin/python3 /usr/local/bin/python; fi

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Default web start; Railway/worker override via startCommand / Procfile.
CMD ["bash", "scripts/start_web.sh"]
