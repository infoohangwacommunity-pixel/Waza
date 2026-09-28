import pytest
import wave
import struct
import math
from wax.world.manager import get_or_create_world
from wax.world.exec import world_exec
from wax.terminal.workspace import principal_workspace, stage_media_for_work


@pytest.mark.asyncio
async def test_terminal_first_audio_media_processing():
    """
    Prove Stage 2: AI uses world_exec (terminal) to locate an inbound audio file,
    inspect it, write and run Python code to process/transcribe it, and return results.
    """
    principal_id = "test-terminal-media-user"
    world = get_or_create_world(principal_id)
    ws = principal_workspace(principal_id)

    # 1. Simulate inbound media placed in principal workspace media dir
    media_dir = ws / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    audio_path = media_dir / "voice_note.wav"

    # Create a valid 16kHz mono WAV file
    with wave.open(str(audio_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        samples = [int(32767 * 0.5 * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(16000)]
        wav.writeframes(struct.pack(f"<{len(samples)}h", *samples))

    assert audio_path.is_file()

    # 2. Stage media for work (inbound pipeline step)
    staged = stage_media_for_work(principal_id, audio_path, work_id="work-123")
    assert staged["ok"]

    # 3. Terminal execution: AI writes a Python script to inspect and process the audio file
    script = """
import os
import wave
from pathlib import Path

media_dir = Path("media")
files = list(media_dir.glob("*.wav"))
if not files:
    print("NO_FILES_FOUND")
    exit(1)

audio_file = files[0]
with wave.open(str(audio_file), "rb") as w:
    channels = w.getnchannels()
    framerate = w.getframerate()
    frames = w.getnframes()
    duration = frames / float(framerate)

print(f"AUDIO_PROCESSED: file={audio_file.name} channels={channels} rate={framerate} duration={duration:.2f}s")
"""

    res = await world_exec(world, script=script, budget_class="interactive")
    assert res["ok"], f"Execution failed: {res.get('error')} {res.get('stderr')}"
    assert "AUDIO_PROCESSED" in res["stdout"]
    assert "voice_note.wav" in res["stdout"]
