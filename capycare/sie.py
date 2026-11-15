"""Superlinked SIE (serverless): every model Capy Care uses runs here.

Primitives used:
  extract  - docling (PDF), LightOnOCR-2-1B (photos), Whisper large-v3 turbo (voice notes),
             GLiNER2 (entities + structured medication facts, no LLM)
  score    - Qwen3-Reranker-4B (ranks drug-label passages)
  encode   - Qwen3-Embedding-4B (Cognee's vectors, via SIE's OpenAI-compatible /v1/embeddings)
  generate - Qwen3.8-27B (agent reasoning, Cognee's LLM, the doctor-visit brief)
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time
from pathlib import Path
from typing import Any, Callable

import httpx
from sie_sdk import SIEClient
from sie_sdk.types import Item

from . import config
from .state import STATE

log = logging.getLogger("capycare.sie")
_API_KEY = config.env("SIE_API_KEY")


def _client() -> SIEClient:
    # SIEClient is synchronous; each worker thread gets its own client.
    return SIEClient(api_key=_API_KEY, timeout_s=300)


async def _timed(op: str, model: str, fn: Callable[[], Any], note: str = "") -> tuple[Any, float]:
    started = time.perf_counter()
    result = await asyncio.to_thread(fn)
    ms = (time.perf_counter() - started) * 1000
    STATE.log_call("SIE", op, model, ms, note)
    return result, ms


# --- reading documents -----------------------------------------------------------------------
async def read_pdf(path: Path) -> tuple[str, str, float]:
    data = path.read_bytes()
    result, ms = await _timed("extract", config.SIE_DOCS, lambda: _client().extract(
        config.SIE_DOCS, Item(document={"data": data, "format": "pdf"})), note=f"PDF → markdown ({path.name})")
    return result["data"]["markdown"], config.SIE_DOCS, ms


async def read_image(path: Path, ocr_timeout_s: float = 45) -> tuple[str, str, float]:
    data = path.read_bytes()
    fmt = "png" if path.suffix.lower() == ".png" else "jpeg"
    try:
        result, ms = await asyncio.wait_for(_timed("extract", config.SIE_OCR, lambda: _client().extract(
            config.SIE_OCR, Item(images=[{"data": data, "format": fmt}])), note=f"photo → text ({path.name})"),
            timeout=ocr_timeout_s)
        return result["entities"][0]["text"], config.SIE_OCR, ms
    except (asyncio.TimeoutError, KeyError, IndexError) as exc:
        log.warning("OCR fallback to Qwen vision: %r", exc)
        text, ms = await describe_image(data, fmt, "Transcribe every line of text on this label exactly.")
        return text, config.SIE_LLM, ms


async def transcribe(path: Path) -> tuple[str, str, float]:
    data = path.read_bytes()
    fmt = path.suffix.lower().lstrip(".") or "mp3"
    result, ms = await _timed("extract", config.SIE_ASR, lambda: _client().extract(
        config.SIE_ASR, Item(audio={"data": data, "format": fmt})), note=f"voice note → transcript ({path.name})")
    return result["data"]["text"].strip(), config.SIE_ASR, ms


async def read_document(path: Path, kind: str) -> tuple[str, str, float]:
    if kind == "pdf":
        return await read_pdf(path)
    if kind == "image":
        return await read_image(path)
    if kind == "audio":
        return await transcribe(path)
    return path.read_text(), "text", 0.0


# --- GLiNER2: entities and structured facts without an LLM ------------------------------------
async def entities(text: str, labels: list[str], note: str = "") -> list[dict[str, Any]]:
    result, _ = await _timed("extract", config.SIE_GLINER, lambda: _client().extract(
        config.SIE_GLINER, Item(text=text), labels=labels), note=note or f"entities: {', '.join(labels)}")
    return [{"label": e.get("label"), "text": e.get("text"), "score": e.get("score")} for e in result.get("entities", [])]


async def structured(text: str, fields: dict[str, Any], note: str = "") -> dict[str, str]:
    """GLiNER2 schema mode. `fields` maps name -> description, or name -> list of allowed values."""
    props: dict[str, Any] = {}
    for name, spec in fields.items():
        if isinstance(spec, list):
            props[name] = {"type": "string", "enum": spec}
        else:
            props[name] = {"type": "string", "description": spec}
    schema = {"type": "object", "properties": props}
    result, _ = await _timed("extract", config.SIE_GLINER, lambda: _client().extract(
        config.SIE_GLINER, Item(text=text), output_schema=schema), note=note or "structured extraction")
    data = result.get("data") or {}
    return {k: (v or "").strip() if isinstance(v, str) else v for k, v in data.items()}


# --- reranking ---------------------------------------------------------------------------------
async def rerank(query: str, passages: list[str], note: str = "") -> list[tuple[int, float]]:
    if not passages:
        return []
    items = [Item(text=p, id=str(i)) for i, p in enumerate(passages)]
    result, _ = await _timed("score", config.SIE_RERANK, lambda: _client().score(
        config.SIE_RERANK, Item(text=query), items), note=note or f"rank {len(passages)} passages")
    ranked = []
    for entry in result.get("scores", []):
        idx = entry.get("item_id", entry.get("itemId", entry.get("id")))
        ranked.append((int(idx), float(entry.get("score", 0.0))))
    return sorted(ranked, key=lambda x: -x[1])


# --- Qwen3.8 on SIE (OpenAI-compatible chat) --------------------------------------------------
async def chat(messages: list[dict[str, str]], max_tokens: int = 700, temperature: float = 0.2, note: str = "") -> str:
    body = {"model": config.SIE_LLM, "messages": messages, "max_completion_tokens": max_tokens,
            "temperature": temperature, "chat_template_kwargs": {"enable_thinking": False}}
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=180) as client:
        for attempt in range(5):  # serverless can briefly answer 429/503 under load; back off and retry
            resp = await client.post(f"{config.SIE_BASE_URL}/v1/chat/completions", json=body,
                                     headers={"Authorization": f"Bearer {_API_KEY}"})
            if resp.status_code not in (429, 500, 502, 503, 504) or attempt == 4:
                break
            log.warning("SIE chat returned %s, retrying (attempt %d)", resp.status_code, attempt + 1)
            await asyncio.sleep(1.5 * (attempt + 1))
        resp.raise_for_status()
    STATE.log_call("SIE", "generate", config.SIE_LLM, (time.perf_counter() - started) * 1000, note or "chat")
    return (resp.json()["choices"][0]["message"]["content"] or "").strip()


async def describe_image(data: bytes, fmt: str, prompt: str) -> tuple[str, float]:
    body = {"prompt": prompt, "max_new_tokens": 400, "temperature": 0,
            "images": [{"data": base64.b64encode(data).decode(), "format": fmt}]}
    started = time.perf_counter()
    model_path = config.SIE_LLM.replace("/", "__")
    async with httpx.AsyncClient(timeout=180) as client:
        resp = await client.post(f"{config.SIE_BASE_URL}/v1/generate/{model_path}", json=body,
                                 headers={"Authorization": f"Bearer {_API_KEY}"})
        resp.raise_for_status()
    ms = (time.perf_counter() - started) * 1000
    STATE.log_call("SIE", "generate", config.SIE_LLM, ms, "image → text (vision fallback)")
    return resp.json().get("text", "").strip(), ms


# --- warm-up -----------------------------------------------------------------------------------
async def warmup() -> dict[str, str]:
    """Touch every model once so the first family request is fast (serverless cold starts)."""
    demo = config.DEMO_DIR
    jobs = {
        "docling": read_pdf(demo / "discharge_summary.pdf"),
        "ocr": read_image(demo / "pill_bottle.jpg", ocr_timeout_s=400),
        "whisper": transcribe(demo / "voice_note_sam.mp3"),
        "gliner2": entities("Lisinopril 5 mg once daily.", ["medication", "dose"], note="warm-up"),
        "reranker": rerank("dizziness", ["Dizziness is common.", "Take with food."], note="warm-up"),
        "qwen": chat([{"role": "user", "content": "Say ready."}], max_tokens=5, note="warm-up"),
    }
    out: dict[str, str] = {}
    results = await asyncio.gather(*jobs.values(), return_exceptions=True)
    for name, res in zip(jobs, results):
        out[name] = "ok" if not isinstance(res, Exception) else f"error: {res!r}"[:160]
    if out["qwen"] != "ok":  # one retry for the LLM after a short pause
        await asyncio.sleep(5)
        try:
            await chat([{"role": "user", "content": "Say ready."}], max_tokens=5, note="warm-up")
            out["qwen"] = "ok"
        except Exception as exc:
            out["qwen"] = f"error: {exc!r}"[:160]
    log.info("SIE warm-up: %s", out)
    return out
