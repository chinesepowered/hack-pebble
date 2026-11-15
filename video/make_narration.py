"""Generate the demo video's narration, one ElevenLabs clip per scene.

Usage (repo root): .venv/bin/python video/make_narration.py [scene_id ...]
Reads ELEVENLABS_API_KEY from .env. Skips scenes whose audio already exists unless named.
"""
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
env = dict(l.strip().split("=", 1) for l in (ROOT / ".env").read_text().splitlines() if "=" in l and not l.startswith("#"))
spec = json.loads((ROOT / "video" / "narration.json").read_text())
out_dir = ROOT / "video" / "audio"
out_dir.mkdir(parents=True, exist_ok=True)
only = set(sys.argv[1:])
scenes = spec["scenes"]

for i, scene in enumerate(scenes):
    target = out_dir / f"{scene['id']}.mp3"
    if target.exists() and scene["id"] not in only:
        continue
    if only and scene["id"] not in only:
        continue
    body = {
        "text": scene["text"],
        "model_id": spec["model_id"],
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.8, "style": 0.3, "use_speaker_boost": True},
        "previous_text": scenes[i - 1]["text"] if i > 0 else None,
        "next_text": scenes[i + 1]["text"] if i + 1 < len(scenes) else None,
    }
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{spec['voice_id']}?output_format=mp3_44100_128",
        data=json.dumps(body).encode(),
        headers={"xi-api-key": env["ELEVENLABS_API_KEY"], "Content-Type": "application/json"})
    target.write_bytes(urllib.request.urlopen(req, timeout=180).read())
    dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(target)],
                         capture_output=True, text=True).stdout.strip()
    print(f"{scene['id']}: {len(scene['text'])} chars -> {float(dur):.1f}s")

total = 0.0
for scene in scenes:
    f = out_dir / f"{scene['id']}.mp3"
    if f.exists():
        total += float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)],
                                      capture_output=True, text=True).stdout.strip())
print(f"total narration: {total:.1f}s")
