"""Write demo/capy-care-demo.srt from the narration, using the same scene timing as make_video.py."""
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAD_BEFORE, PAD_AFTER = 0.25, 0.6
EXTRA = {"05_demo_resolve": 1.5}  # matches the extra graph hold in make_video.py
LEAD = {"01_title": 0.8}
FIXES = [("Gliner 2", "GLiNER2"), ("Kwen", "Qwen"), ("LightOn OCR", "LightOnOCR"), ("A.I.", "AI"),
         ("five milligrams", "5 mg"), ("November tenth", "November 10")]

spec = json.loads((ROOT / "video" / "narration.json").read_text())


def probe(path: Path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                                capture_output=True, text=True, check=True).stdout.strip())


def stamp(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def chunks(sentence: str, limit: int = 84) -> list[str]:
    if len(sentence) <= limit:
        return [sentence]
    out, cur = [], ""
    for part in re.split(r"(?<=,)\s+", sentence):
        if cur and len(cur) + 1 + len(part) > limit:
            out.append(cur)
            cur = part
        else:
            cur = f"{cur} {part}".strip()
    return out + ([cur] if cur else [])


def wrap(text: str, width: int = 44) -> str:
    if len(text) <= width:
        return text
    cut = text.rfind(" ", 0, len(text) // 2 + 8)
    return text[:cut] + "\n" + text[cut + 1:]


cues, t = [], 0.0
for scene in spec["scenes"]:
    audio = probe(ROOT / "video" / "audio" / f"{scene['id']}.mp3")
    lead = LEAD.get(scene["id"], PAD_BEFORE)
    dur = lead + audio + PAD_AFTER + EXTRA.get(scene["id"], 0)
    text = scene["text"]
    for a, b in FIXES:
        text = text.replace(a, b)
    pieces = [c for s in re.split(r"(?<=[.?!])\s+", text) for c in chunks(s)]
    per_char = audio / max(1, sum(len(p) for p in pieces))
    start = t + lead
    for piece in pieces:
        end = start + len(piece) * per_char
        cues.append((start, end, wrap(piece)))
        start = end
    t += dur

srt = "\n".join(f"{i}\n{stamp(a)} --> {stamp(b)}\n{txt}\n" for i, (a, b, txt) in enumerate(cues, 1))
(ROOT / "demo" / "capy-care-demo.srt").write_text(srt)
print(f"{len(cues)} cues, ends at {stamp(t)}")
