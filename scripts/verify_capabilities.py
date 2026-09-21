#!/usr/bin/env python3
"""Report which terminal OS binaries are available (non-fatal diagnostic)."""
from __future__ import annotations

import shutil
import sys

REQUIRED_FOR_MEDIA = [
    "ffmpeg",
    "ffprobe",
    "tesseract",
    "sox",
    "mediainfo",
    "pdftotext",
    "file",
]
OPTIONAL_ISOLATION = ["bwrap", "docker"]

def main() -> int:
    missing = []
    print("=== WAX TERMINAL CAPABILITY PROBE ===")
    for name in REQUIRED_FOR_MEDIA:
        path = shutil.which(name)
        status = path or "MISSING"
        print(f"  {name}: {status}")
        if not path:
            missing.append(name)
    for name in OPTIONAL_ISOLATION:
        path = shutil.which(name)
        print(f"  {name}: {path or 'MISSING (optional)'}")
    if missing:
        print(
            "WARN: media tools missing — inspect_media / OCR / audio tools will fail until "
            "nixpacks.toml / Aptfile packages are installed in the image."
        )
        print("missing:", ", ".join(missing))
        # Non-fatal: do not block startup; tutor must not hallucinate tool success
        return 0
    print("OK: required media binaries present")
    return 0

if __name__ == "__main__":
    sys.exit(main())
