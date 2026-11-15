"""Capy Care web app: runs the agent crew and serves the family dashboard.

Run from the repo root:  .venv/bin/uvicorn capycare.app:app --port 8000
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import band_api, config, sie
from .crew import HUMAN, IDS, NAMES, run_crew
from .memory import MEMORY
from .state import STATE

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
for noisy in ("httpx", "band", "cognee", "LiteLLM", "litellm"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger("capycare.app")

SPONSOR_TAG = re.compile(r"^\[(SIE|Cognee|Keenable|Band)\]\s*")
_runtime: dict[str, object] = {"warm": "starting"}


async def new_case() -> None:
    """Fresh case: empty state, empty Cognee dataset, new Band room with the four capys."""
    human_id = STATE.human_id
    STATE.reset()
    STATE.human_id = human_id
    await MEMORY.reset()
    if config.VAULT_DIR.exists():
        shutil.rmtree(config.VAULT_DIR)
    config.VAULT_DIR.mkdir(parents=True, exist_ok=True)
    STATE.room_id = await band_api.create_room(f"{config.PATIENT.split()[0]}'s Care Team",
                                               [IDS[k] for k in config.AGENT_KEYS])
    STATE.touch()
    log.info("new case in Band room %s", STATE.room_id)


def _normalize(m: dict) -> dict:
    names = {v: NAMES[k] for k, v in IDS.items()}
    if STATE.human_id:
        names[STATE.human_id] = HUMAN
    agent = next((k for k, v in IDS.items() if v == m.get("sender_id")), None)
    content = band_api.render_mentions(m.get("content", ""), names)
    sponsor = ((m.get("metadata") or {}).get("sponsor")) if isinstance(m.get("metadata"), dict) else None
    tag = SPONSOR_TAG.match(content)
    if tag:
        sponsor, content = tag.group(1), content[tag.end():]
    return {"id": m["id"], "type": m.get("message_type"), "agent": agent,
            "sender": HUMAN if m.get("sender_type") == "User" else (m.get("sender_name") or "agent"),
            "content": content, "at": m.get("inserted_at"), "sponsor": sponsor}


async def poll_room() -> None:
    """Mirror the Band room into the dashboard state."""
    while True:
        try:
            if STATE.room_id:
                raw = await band_api.list_messages(STATE.room_id, limit=100)
                msgs = [_normalize(m) for m in raw if m.get("message_type") in ("text", "thought", "tool_call", "error")]
                if [m["id"] for m in msgs] != [m["id"] for m in STATE.messages]:
                    STATE.messages = msgs
                    STATE.touch()
        except Exception as exc:  # network blips should not kill the mirror
            log.warning("room poll failed: %r", exc)
        await asyncio.sleep(1.0)


async def warm_up() -> None:
    _runtime["warm"] = "warming"
    result = await sie.warmup()
    STATE.calls.clear()
    STATE.touch()
    _runtime["warm"] = "ready" if all(v == "ok" for v in result.values()) else f"partial: {result}"


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    STATE.human_id = (await band_api.me())["id"]
    await new_case()
    tasks = [asyncio.create_task(run_crew()), asyncio.create_task(poll_room()), asyncio.create_task(warm_up())]
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(title="Capy Care", lifespan=lifespan)
DEMO_TOKEN = os.environ.get("CAPY_DEMO_TOKEN", "")


@app.middleware("http")
async def demo_guard(request, call_next):
    """When a demo token is set (public tunnel), anyone can watch but only token holders can act."""
    if DEMO_TOKEN and request.method == "POST":
        token = request.headers.get("x-demo-token") or request.query_params.get("t")
        if token != DEMO_TOKEN:
            return JSONResponse({"error": "This is a view-only link."}, status_code=403)
    return await call_next(request)


app.mount("/web", StaticFiles(directory=config.WEB_DIR), name="web")
app.mount("/brand", StaticFiles(directory=config.ROOT / "brand"), name="brand")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(config.WEB_DIR / "index.html")


@app.get("/api/state")
async def state() -> JSONResponse:
    data = STATE.to_dict()
    data["warm"] = _runtime["warm"]
    data["requires_token"] = bool(DEMO_TOKEN)
    data["human"] = HUMAN
    data["patient"] = config.PATIENT
    return JSONResponse(data)


@app.post("/api/reset")
async def reset() -> dict:
    await new_case()
    return {"room_id": STATE.room_id}


def _store(src: Path, name: str | None = None) -> Path:
    dest = config.VAULT_DIR / f"{uuid.uuid4().hex[:6]}-{name or src.name}"
    shutil.copy(src, dest)
    return dest


def _kind(name: str) -> str:
    ext = Path(name).suffix.lower()
    return {".pdf": "pdf", ".jpg": "image", ".jpeg": "image", ".png": "image",
            ".mp3": "audio", ".m4a": "audio", ".wav": "audio"}.get(ext, "text")


@app.post("/api/share/documents")
async def share_documents(files: list[UploadFile] | None = File(None), note: str = Form("")) -> dict:
    """The family shares documents (defaults to the demo discharge summary + pill bottle photo)."""
    names = []
    if files:
        for f in files:
            dest = config.VAULT_DIR / f"{uuid.uuid4().hex[:6]}-{f.filename}"
            dest.write_bytes(await f.read())
            STATE.add_document(f.filename, str(dest), _kind(f.filename), HUMAN)
            names.append(f.filename)
    else:
        for name in ("discharge_summary.pdf", "pill_bottle.jpg"):
            STATE.add_document(name, str(_store(config.DEMO_DIR / name)), _kind(name), HUMAN)
            names.append(name)
    text = note or ("Mom's discharge papers from Riverside General and her new pill bottle from Maple Pharmacy "
                    "are in the family vault. Can you read them?")
    await band_api.send_as_human(STATE.room_id, text, [IDS["READER"]])
    return {"shared": names}


@app.post("/api/share/voice-note")
async def share_voice_note() -> dict:
    name = "voice_note_sam.mp3"
    STATE.add_document(name, str(_store(config.DEMO_DIR / name)), "audio", HUMAN, note_date="2026-11-13")
    await band_api.send_as_human(STATE.room_id, "Forwarding a voice note my brother Sam sent last night about Mom.",
                                 [IDS["READER"]])
    return {"shared": [name]}


class Reply(BaseModel):
    text: str


@app.post("/api/reply")
async def reply(body: Reply) -> dict:
    await band_api.send_as_human(STATE.room_id, body.text, [IDS["GUARDIAN"]])
    return {"ok": True}


@app.get("/api/doc/{doc_id}")
async def document(doc_id: str) -> FileResponse:
    doc = next(d for d in STATE.documents if d.id == doc_id)
    return FileResponse(doc.path)
