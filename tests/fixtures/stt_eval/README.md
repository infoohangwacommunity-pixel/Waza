# STT evaluation fixtures

Add Nigerian-English samples as subdirectories:

```
tests/fixtures/stt_eval/
  ng_chemistry_01/
    audio.ogg          # WhatsApp-quality voice note
    reference.txt      # human transcript
    meta.json          # {"accent":"nigerian_english","domain":"chemistry","notes":"..."}
  ng_jamb_vocab_01/
    ...
```

Run:
  python scripts/eval_stt.py --fixtures tests/fixtures/stt_eval --report /tmp/stt_report.json

Do not hardcode accent-specific production workflows. This harness only measures quality.
