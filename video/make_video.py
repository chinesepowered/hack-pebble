"""Assemble the Capy Care demo video from narration, rendered cards and a recorded live run.

Usage (repo root):
  .venv/bin/python video/make_video.py <assets_dir> <recording_dir> <out.mp4>

The live footage is time-remapped piecewise so that each on-screen moment (documents read, Cognee
conflict found, family reply, temporal recall, brief written) lands on the narration line that
describes it. Stretches played faster than real time get a "sped up" badge.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS, REC, OUT = (Path(p).resolve() for p in sys.argv[1:4])
WORK = REC / "build"
WORK.mkdir(exist_ok=True)
FPS = 30
CREAM = "0xFFF6EA"
PAD_BEFORE, PAD_AFTER = 0.25, 0.6

spec = json.loads((ROOT / "video" / "narration.json").read_text())
timeline = json.loads((REC / "timeline.json").read_text())
ev, frames = timeline["events"], timeline["frames"]


def probe(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def phrase_starts(text: str, parts: list[str], audio: float) -> list[float]:
    """Estimate when each narration part starts, by character share of the clip."""
    per_char = audio / len(text)
    starts, pos = [], 0
    for part in parts:
        idx = text.index(part, pos)
        starts.append(PAD_BEFORE + idx * per_char)
        pos = idx + len(part)
    return starts


def audio_args(mp3: Path, dur: float, lead: float) -> list[str]:
    ms = int(lead * 1000)
    return ["-i", str(mp3), "-filter:a", f"adelay={ms}|{ms},apad", "-t", f"{dur:.3f}", "-ar", "48000", "-ac", "2",
            "-c:a", "aac", "-b:a", "192k"]


def still_scene(image: Path, mp3: Path, out: Path, lead: float = PAD_BEFORE, zoom: float = 0.035) -> float:
    dur = lead + probe(mp3) + PAD_AFTER
    n = int(round(dur * FPS))
    vf = (f"scale=3840:2160,zoompan=z='1+{zoom}*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1920x1080:fps={FPS},"
          f"fade=t=in:st=0:d=0.35:color={CREAM},fade=t=out:st={dur - 0.35:.3f}:d=0.35:color={CREAM},format=yuv420p")
    run(["ffmpeg", "-y", "-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", str(image)]
        + ["-i", str(mp3), "-filter_complex", f"[0:v]{vf}[v];[1:a]adelay={int(lead * 1000)}|{int(lead * 1000)},apad[a]",
           "-map", "[v]", "-map", "[a]", "-t", f"{dur:.3f}", "-r", str(FPS), "-c:v", "libx264", "-crf", "18",
           "-preset", "medium", "-ar", "48000", "-ac", "2", "-c:a", "aac", "-b:a", "192k", str(out)])
    return dur


def remap(anchors: list[tuple[float, float]], t: float) -> float:
    if t <= anchors[0][0]:
        return anchors[0][1]
    for (r0, o0), (r1, o1) in zip(anchors, anchors[1:]):
        if t <= r1:
            return o0 + (t - r0) * (o1 - o0) / (r1 - r0)
    return anchors[-1][1]


def demo_scene(sid: str, anchors: list[tuple[float, float]], captions: list[tuple[str, float, float]], out: Path) -> float:
    mp3 = ROOT / "video" / "audio" / f"{sid}.mp3"
    dur = anchors[-1][1]
    r_start, r_end = anchors[0][0], anchors[-1][0]
    sel = [f for f in frames if r_start - 0.5 <= f["t"] <= r_end + 0.5]
    lines, ff_windows = [], []
    for cur, nxt in zip(sel, sel[1:] + [None]):
        o0 = remap(anchors, cur["t"])
        o1 = remap(anchors, nxt["t"]) if nxt else dur
        if o1 - o0 <= 0.0005:
            continue
        lines += [f"file '{cur['file']}'", f"duration {o1 - o0:.4f}"]
    lines.append(f"file '{sel[-1]['file']}'")
    lst = WORK / f"{sid}.txt"
    lst.write_text("\n".join(lines) + "\n")
    for (r0, o0), (r1, o1) in zip(anchors, anchors[1:]):
        if (r1 - r0) / max(o1 - o0, 1e-6) > 1.6:
            ff_windows.append((o0, o1))
    raw = WORK / f"{sid}_raw.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-vf",
         f"fps={FPS},scale=1728:972:flags=lanczos,format=yuv420p", "-t", f"{dur:.3f}", "-c:v", "libx264", "-crf", "16", str(raw)])
    inputs = ["-loop", "1", "-framerate", str(FPS), "-i", str(ASSETS / "frame.png"), "-i", str(raw)]
    graph = [f"[0:v][1:v]overlay=96:100:shortest=1[v0]"]
    label, k = "v0", 2
    for name, a, b in captions:
        inputs += ["-loop", "1", "-framerate", str(FPS), "-i", str(ASSETS / f"{name}.png")]
        graph.append(f"[{label}][{k}:v]overlay=0:0:enable='between(t,{a:.3f},{b:.3f})'[v{k}]")
        label, k = f"v{k}", k + 1
    for a, b in ff_windows:
        inputs += ["-loop", "1", "-framerate", str(FPS), "-i", str(ASSETS / "cap-ff.png")]
        graph.append(f"[{label}][{k}:v]overlay=0:0:enable='between(t,{a:.3f},{b:.3f})'[v{k}]")
        label, k = f"v{k}", k + 1
    graph.append(f"[{label}]fade=t=in:st=0:d=0.3:color={CREAM},fade=t=out:st={dur - 0.3:.3f}:d=0.3:color={CREAM},"
                 f"format=yuv420p[vout]")
    inputs += ["-i", str(mp3)]
    graph.append(f"[{k}:a]adelay={int(PAD_BEFORE * 1000)}|{int(PAD_BEFORE * 1000)},apad[aout]")
    run(["ffmpeg", "-y"] + inputs + ["-filter_complex", ";".join(graph), "-map", "[vout]", "-map", "[aout]",
         "-t", f"{dur:.3f}", "-r", str(FPS), "-c:v", "libx264", "-crf", "18", "-preset", "medium",
         "-ar", "48000", "-ac", "2", "-c:a", "aac", "-b:a", "192k", str(out)])
    return dur


scenes = {s["id"]: s for s in spec["scenes"]}
audio = {sid: probe(ROOT / "video" / "audio" / f"{sid}.mp3") for sid in scenes}
clips: list[Path] = []
total = 0.0

# 1-3: title, problem, crew
for sid, img, lead in [("01_title", "title.png", 0.8), ("02_problem", "slide1.png", PAD_BEFORE), ("03_crew", "slide2.png", PAD_BEFORE)]:
    out = WORK / f"{sid}.mp4"
    total += still_scene(ASSETS / img, ROOT / "video" / "audio" / f"{sid}.mp3", out, lead=lead)
    clips.append(out)

# 4: share papers -> SIE reads -> Cognee conflict -> Keenable label -> Guardian asks
sid = "04_demo_conflict"
text = scenes[sid]["text"]
p2, p3, p4 = phrase_starts(text, ["SIE reads each one", "Elder Capy stores", "The Pharmacist reads"], audio[sid])
d4 = PAD_BEFORE + audio[sid] + PAD_AFTER
anchors = [(ev["docs"] - 3.0, 0.0), (ev["docs"], 3.0), (ev["elder"], p3), (ev["conflict"], p4 + 1.0), (ev["ask"] + 3.0, d4)]
total += demo_scene(sid, anchors, [("cap-1a", 0, p3), ("cap-1b", p3, p4 + 1.0), ("cap-1c", p4 + 1.0, d4)], WORK / f"{sid}.mp4")
clips.append(WORK / f"{sid}.mp4")

# 5: family confirms -> Cognee resolve_conflict
sid = "05_demo_resolve"
text = scenes[sid]["text"]
(q2,) = phrase_starts(text, ["Gliner 2 reads"], audio[sid])
d5 = PAD_BEFORE + audio[sid] + PAD_AFTER
anchors = [(ev["typing"] - 0.8, 0.0), (ev["reply"], q2), (ev["resolved"], d5 - 2.5), (ev["resolved"] + 2.5, d5)]
total += demo_scene(sid, anchors, [("cap-2a", 0, q2), ("cap-2b", q2, d5)], WORK / f"{sid}.mp4")
clips.append(WORK / f"{sid}.mp4")

# 6: voice note -> Whisper -> Cognee TEMPORAL -> label -> brief
sid = "06_demo_temporal"
text = scenes[sid]["text"]
r2, r3, r4 = phrase_starts(text, ["Whisper on SIE", "Elder Capy asks", "The official label"], audio[sid])
d6 = PAD_BEFORE + audio[sid] + PAD_AFTER
anchors = [(ev["voice"] - 1.0, 0.0), (ev["voice"], 2.5), (ev["voice"] + 4.0, r3), (ev["temporal"], r3 + (r4 - r3) * 0.55),
           (ev["brief"], d6 - 5.0), (ev["end"], d6)]
total += demo_scene(sid, anchors, [("cap-3a", 0, r3), ("cap-3b", r3, r4), ("cap-3c", r4, d6)], WORK / f"{sid}.mp4")
clips.append(WORK / f"{sid}.mp4")

# 7-8: how it works, close
for sid, img, zoom in [("07_how", "slide3.png", 0.03), ("08_close", "close.png", 0.025)]:
    out = WORK / f"{sid}.mp4"
    total += still_scene(ASSETS / img, ROOT / "video" / "audio" / f"{sid}.mp3", out, zoom=zoom)
    clips.append(out)

concat = WORK / "concat.txt"
concat.write_text("".join(f"file '{c}'\n" for c in clips))
run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", "-movflags", "+faststart", str(OUT)])
print(f"wrote {OUT} ({probe(OUT):.1f}s; scenes sum {total:.1f}s)")
