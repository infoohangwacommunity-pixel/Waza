#!/usr/bin/env bash
# Provision Vosk STT model for offline transcription.
# Usage:
#   BUILD-TIME (Dockerfile):  scripts/provision_vosk_model.sh /opt/wax-models
#   RUNTIME (volume seed):    scripts/provision_vosk_model.sh "$WAX_MODEL_ROOT"
#
# Production rule: never download at request time. Bake into image at /opt/wax-models
# (outside the /data volume mount) and seed the volume on first boot if empty.
set -euo pipefail

MODEL_NAME="${WAX_VOSK_MODEL_NAME:-vosk-model-small-en-us-0.15}"
DEST_ROOT="${1:-${WAX_MODEL_ROOT:-/opt/wax-models}}"
MODEL_DIR="${DEST_ROOT}/models/vosk/${MODEL_NAME}"
URL="https://alphacephei.com/vosk/models/${MODEL_NAME}.zip"

mkdir -p "${DEST_ROOT}/models/vosk"

if [[ -d "${MODEL_DIR}" ]] && [[ -n "$(ls -A "${MODEL_DIR}" 2>/dev/null || true)" ]]; then
  echo "vosk_model_present path=${MODEL_DIR}"
  exit 0
fi

# Also accept any already-extracted vosk-model-* under models/vosk
for child in "${DEST_ROOT}/models/vosk"/vosk-model-*; do
  if [[ -d "${child}" ]] && [[ -n "$(ls -A "${child}" 2>/dev/null || true)" ]]; then
    echo "vosk_model_present path=${child}"
    exit 0
  fi
done

echo "vosk_model_download_start url=${URL} dest=${DEST_ROOT}"
TMP_ZIP="$(mktemp /tmp/vosk-XXXXXX.zip)"
trap 'rm -f "${TMP_ZIP}"' EXIT

if command -v curl >/dev/null 2>&1; then
  curl -fsSL --retry 5 --retry-delay 2 -o "${TMP_ZIP}" "${URL}"
elif command -v wget >/dev/null 2>&1; then
  wget -q -O "${TMP_ZIP}" "${URL}"
else
  echo "FATAL: neither curl nor wget available to fetch Vosk model" >&2
  exit 1
fi

unzip -q -o "${TMP_ZIP}" -d "${DEST_ROOT}/models/vosk"
# Normalize name if zip extracts with slightly different layout
if [[ ! -d "${MODEL_DIR}" ]]; then
  found="$(find "${DEST_ROOT}/models/vosk" -maxdepth 1 -type d -name 'vosk-model-*' | head -1 || true)"
  if [[ -n "${found}" ]] && [[ "${found}" != "${MODEL_DIR}" ]]; then
    ln -sfn "$(basename "${found}")" "${MODEL_DIR}" 2>/dev/null || mv "${found}" "${MODEL_DIR}"
  fi
fi

if [[ -d "${MODEL_DIR}" ]] || find "${DEST_ROOT}/models/vosk" -maxdepth 1 -type d -name 'vosk-model-*' | grep -q .; then
  echo "vosk_model_ready under ${DEST_ROOT}/models/vosk"
  du -sh "${DEST_ROOT}/models/vosk"/* 2>/dev/null || true
  exit 0
fi

echo "FATAL: Vosk model extract failed" >&2
exit 1
