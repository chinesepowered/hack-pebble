"""Record a real Capy Care run as timed state changes, for the public replay page.

Usage (repo root, server running on :8000): .venv/bin/python video/record_state.py <out.json>
Drives the three family actions through the API and stores, for each moment the dashboard state
changed, only the parts that changed.
"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8000"
REPLY = "Just called Maple Pharmacy. Dr. Alvarez increased it to 10 mg at Mom's follow-up on November 10, so 10 mg is correct."
KEYS = ["messages", "medications", "conflicts", "timeline", "graph", "brief", "call_totals", "calls", "agent_status", "pending",
        "sources", "evidence"]


def get() -> dict:
    return json.load(urllib.request.urlopen(f"{BASE}/api/state", timeout=20))


def post(path: str, body: dict | None = None) -> None:
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method="POST",
                                 headers={"Content-Type": "application/json"} if body else {})
    urllib.request.urlopen(req, timeout=30).read()


frames, last, t0, steps = [], {}, None, {}


def poll_until(done, timeout: float) -> None:
    start = time.time()
    while time.time() - start < timeout:
        s = get()
        changed = {k: s[k] for k in KEYS if json.dumps(s.get(k), sort_keys=True) != json.dumps(last.get(k), sort_keys=True)}
        if changed:
            frames.append({"t": round(time.time() - t0, 2), "set": changed})
            last.update(changed)
        if done(s):
            return
        time.sleep(0.4)
    raise SystemExit("timed out")


post("/api/reset")
time.sleep(2)
t0 = time.time()
poll_until(lambda s: True, 5)
steps["docs"] = round(time.time() - t0, 2); post("/api/share/documents")
poll_until(lambda s: (s.get("pending") or {}).get("type") == "confirm_dose", 400)
steps["ask"] = round(time.time() - t0, 2)
time.sleep(1.5)
steps["reply"] = round(time.time() - t0, 2); post("/api/reply", {"text": REPLY})
poll_until(lambda s: any(c.get("resolution_source") == "user" for c in s["conflicts"]) and not s.get("pending"), 300)
steps["resolved"] = round(time.time() - t0, 2)
time.sleep(1.5)
steps["voice"] = round(time.time() - t0, 2); post("/api/share/voice-note")
poll_until(lambda s: bool(s.get("brief")), 400)
time.sleep(4)
poll_until(lambda s: True, 2)
steps["end"] = round(time.time() - t0, 2)
final = get()
out = {"recorded_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "steps": steps, "frames": frames,
       "patient": final["patient"], "human": final["human"],
       "conflict_by": [c.get("detected_by", "cognee") for c in final["conflicts"]]}
open(sys.argv[1], "w").write(json.dumps(out))
print(f"{len(frames)} state changes over {steps['end']}s; steps {steps}; conflict found by {out['conflict_by']}")
