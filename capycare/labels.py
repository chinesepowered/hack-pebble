"""Keenable: live web search + page fetch, used to read official drug labels (DailyMed)."""
from __future__ import annotations

import re
import time
from typing import Any

import httpx

from . import config
from .state import STATE

_HEADERS = {"X-API-Key": config.env("KEENABLE_API_KEY")}
_label_cache: dict[str, dict[str, Any]] = {}


async def find_label(drug: str) -> dict[str, Any]:
    """Find the official FDA label for a drug on DailyMed."""
    if drug in _label_cache:
        return _label_cache[drug]
    body = {"query": f"{drug} tablets prescribing information label", "site": "dailymed.nlm.nih.gov", "max_results": 3}
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{config.KEENABLE_API}/search", json=body, headers=_HEADERS)
        resp.raise_for_status()
    results = resp.json()["results"]
    STATE.log_call("Keenable", "search", "site:dailymed.nlm.nih.gov", (time.perf_counter() - started) * 1000,
                   f"official label for {drug}")
    best = next((r for r in results if "setid" in r["url"]), results[0])
    _label_cache[drug] = {"url": best["url"], "title": best["title"]}
    return _label_cache[drug]


async def ask_label(url: str, question: str, max_chars: int = 3000) -> str:
    """Fetch the label page and keep only the parts that answer the question."""
    params = {"url": url, "prompt": question, "max_chars": max_chars}
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(f"{config.KEENABLE_API}/fetch", params=params, headers=_HEADERS)
        resp.raise_for_status()
    STATE.log_call("Keenable", "fetch", "DailyMed label", (time.perf_counter() - started) * 1000, question[:70])
    return resp.json().get("content", "")


def passages(text: str) -> list[str]:
    """Split fetched label text into short, quotable passages."""
    parts = re.split(r"\n+|(?<=[.;])\s+(?=[A-Z])", text)
    cleaned = [re.sub(r"^[-*•\s]+", "", p).strip() for p in parts]
    return [p for p in cleaned if 25 <= len(p) <= 400][:24]
