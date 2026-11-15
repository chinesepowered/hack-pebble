"""Build replay/capy-care-replay.html: a public, phone-friendly replay of a real recorded run.

Usage (repo root): .venv/bin/python video/build_replay.py .logs/replay_state.json
"""
import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
rec = json.loads(Path(sys.argv[1]).read_text())

# Messages only ever grow during a case, so ship them as appended deltas to keep the page small.
frames, sent = [], []
for f in rec["frames"]:
    s = dict(f["set"])
    out = {"t": f["t"], "set": {}}
    if "messages" in s:
        msgs = s.pop("messages")
        if [m["id"] for m in msgs[:len(sent)]] == [m["id"] for m in sent]:
            out["add"] = msgs[len(sent):]
        else:
            out["reset"] = msgs
        sent = msgs
    for key in ("sources",):  # long document text is not shown in the replay
        if key in s:
            s[key] = [{k: v for k, v in src.items() if k != "text"} for src in s[key]]
    out["set"] = s
    frames.append(out)

avatars = {}
for key, name in [("READER", "reader"), ("ELDER", "elder"), ("PHARMACIST", "pharmacist"), ("GUARDIAN", "guardian")]:
    svg = (ROOT / "web" / "img" / f"capy-{name}.svg").read_bytes()
    avatars[key] = "data:image/svg+xml;base64," + base64.b64encode(svg).decode()

data = {"recorded_at": rec["recorded_at"], "steps": rec["steps"], "frames": frames,
        "human": rec["human"], "patient": rec["patient"], "avatars": avatars}
template = (ROOT / "video" / "replay_template.html").read_text()
html = template.replace("__REPLAY_DATA__", json.dumps(data).replace("</", "<\\/"))
out = ROOT / "replay" / "capy-care-replay.html"
out.parent.mkdir(exist_ok=True)
out.write_text(html)
print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB, {len(frames)} frames)")
