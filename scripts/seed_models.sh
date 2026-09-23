#!/usr/bin/env bash
# Runtime seed: if volume WAX_MODEL_ROOT is empty but image has /opt/wax-models, copy once.
# Avoids "bake into /data then mount blank volume hides files".
set -euo pipefail

IMAGE_MODELS="${WAX_IMAGE_MODEL_ROOT:-/opt/wax-models}"
VOL_MODELS="${WAX_MODEL_ROOT:-/data/wax-models}"
MODEL_NAME="${WAX_VOSK_MODEL_NAME:-vosk-model-small-en-us-0.15}"

mkdir -p "${VOL_MODELS}/models/vosk"

vol_has=0
if [[ -d "${VOL_MODELS}/models/vosk/${MODEL_NAME}" ]] && [[ -n "$(ls -A "${VOL_MODELS}/models/vosk/${MODEL_NAME}" 2>/dev/null || true)" ]]; then
  vol_has=1
fi
if find "${VOL_MODELS}/models/vosk" -maxdepth 1 -type d -name 'vosk-model-*' 2>/dev/null | grep -q .; then
  vol_has=1
fi

if [[ "${vol_has}" -eq 1 ]]; then
  echo "seed_models: volume already has model at ${VOL_MODELS}"
  exit 0
fi

if [[ -d "${IMAGE_MODELS}/models/vosk" ]] && find "${IMAGE_MODELS}/models/vosk" -maxdepth 1 -type d -name 'vosk-model-*' 2>/dev/null | grep -q .; then
  echo "seed_models: copying from ${IMAGE_MODELS} → ${VOL_MODELS}"
  mkdir -p "${VOL_MODELS}/models"
  cp -a "${IMAGE_MODELS}/models/vosk" "${VOL_MODELS}/models/" || {
    echo "seed_models: copy failed (volume may be read-only); relying on IMAGE path" >&2
    exit 0
  }
  echo "seed_models: done"
  exit 0
fi

echo "seed_models: no image model and empty volume — STT will be unavailable until model is provisioned"
exit 0
