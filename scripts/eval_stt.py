#!/usr/bin/env python3
"""
Nigerian-English / general STT evaluation harness.

Does not hardcode Nigerian workflows into production. Measures:
  mean quality status, confidence, WER against reference transcripts.

Usage:
  python scripts/eval_stt.py --fixtures tests/fixtures/stt_eval
  python scripts/eval_stt.py --fixtures tests/fixtures/stt_eval --report /tmp/stt_report.json

Fixture layout (per sample directory):
  audio.wav | audio.ogg | ...
  reference.txt   (optional ground truth)
  meta.json       (optional: accent, domain, notes)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _wer(ref: str, hyp: str) -> float | None:
    r = ref.lower().split()
    h = hyp.lower().split()
    if not r:
        return None
    # classic Levenshtein on tokens
    n, m = len(r), len(h)
    dp = list(range(m + 1))
    for i in range(1, n + 1):
        prev, dp = dp, [i] + [0] * m
        for j in range(1, m + 1):
            cost = 0 if r[i - 1] == h[j - 1] else 1
            dp[j] = min(prev[j] + 1, dp[j - 1] + 1, prev[j - 1] + cost)
    return dp[m] / max(1, n)


async def eval_one(audio: Path, reference: str | None) -> dict:
    from wax.tools.transcription import transcribe_local_audio

    # eval may run outside workspace; temporarily disable path guard by
    # copying into a scratch workspace is ideal — for harness we call internal
    # after noting path restriction. Prefer files under a workspace.
    result = await transcribe_local_audio(str(audio))
    out = {
        "audio": str(audio),
        "ok": result.get("ok"),
        "quality": (result.get("quality") or {}).get("status"),
        "mean_word_conf": (result.get("quality") or {}).get("mean_word_conf"),
        "chars": len(result.get("transcript") or ""),
        "transcript": (result.get("transcript") or "")[:500],
        "error": result.get("error"),
        "provider": result.get("provider"),
        "model": result.get("model"),
    }
    if reference is not None:
        out["reference"] = reference[:500]
        out["wer"] = _wer(reference, result.get("transcript") or "")
    return out


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", type=Path, required=True)
    ap.add_argument("--report", type=Path, default=None)
    args = ap.parse_args()
    root: Path = args.fixtures
    if not root.is_dir():
        print(f"fixtures dir missing: {root}", file=sys.stderr)
        return 2

    results = []
    for sample in sorted(root.iterdir()):
        if not sample.is_dir():
            continue
        audio = None
        for cand in sample.iterdir():
            if cand.suffix.lower() in {".wav", ".ogg", ".mp3", ".m4a", ".opus", ".flac"}:
                audio = cand
                break
        if audio is None:
            continue
        ref = None
        ref_path = sample / "reference.txt"
        if ref_path.is_file():
            ref = ref_path.read_text(encoding="utf-8", errors="replace").strip()
        print(f"eval {sample.name} ...", flush=True)
        try:
            results.append(await eval_one(audio, ref))
        except Exception as e:
            results.append({"audio": str(audio), "ok": False, "error": str(e)[:300]})

    summary = {
        "n": len(results),
        "ok": sum(1 for r in results if r.get("ok")),
        "usable": sum(1 for r in results if r.get("quality") == "usable"),
        "uncertain": sum(1 for r in results if r.get("quality") == "uncertain"),
        "unusable": sum(1 for r in results if r.get("quality") == "unusable"),
        "mean_wer": None,
    }
    wers = [r["wer"] for r in results if r.get("wer") is not None]
    if wers:
        summary["mean_wer"] = sum(wers) / len(wers)

    report = {"summary": summary, "results": results}
    print(json.dumps(summary, indent=2))
    if args.report:
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"wrote {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
