"""Band Human API: the family member's side of the room (create room, send messages, read history)."""
from __future__ import annotations

import re
from typing import Any

import httpx

from . import config

_HEADERS = {"X-API-Key": config.env("BAND_USER_API_KEY")}
MENTION_TOKEN = re.compile(r"@\[\[([0-9a-f-]{36})\]\]")


async def _request(method: str, path: str, **kwargs) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.request(method, f"{config.BAND_API}{path}", headers=_HEADERS, **kwargs)
        resp.raise_for_status()
        return resp.json()


async def me() -> dict[str, Any]:
    return (await _request("GET", "/me"))["data"]["user"]


async def create_room(title: str, agent_ids: list[str]) -> str:
    body = {"chat": {"title": title, "participants": [{"participant_id": a} for a in agent_ids]}}
    return (await _request("POST", "/me/chats", json=body))["data"]["id"]


async def send_as_human(room_id: str, content: str, mention_ids: list[str]) -> dict[str, Any]:
    body = {"message": {"content": content, "mentions": [{"id": m} for m in mention_ids]}}
    return (await _request("POST", f"/me/chats/{room_id}/messages", json=body))["data"]


async def list_messages(room_id: str, limit: int = 100) -> list[dict[str, Any]]:
    data = (await _request("GET", f"/me/chats/{room_id}/messages", params={"limit": limit}))["data"]
    return list(reversed(data))  # oldest first


def render_mentions(text: str, names: dict[str, str]) -> str:
    return MENTION_TOKEN.sub(lambda m: "@" + names.get(m.group(1), "someone"), text)
