"""Shared case state for the crew and the dashboard.

Band carries the conversation and the handoffs between agents. This object holds what the agents
work on together: the family's document vault, the facts read from each source, the conflicts
Cognee found, the timeline, the evidence, and the doctor-visit brief.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Document:
    id: str
    name: str
    path: str
    kind: str  # pdf | image | audio | text
    shared_by: str
    note_date: str | None = None  # ISO date given by the family (used for voice notes)
    read: bool = False


@dataclass
class MedFact:
    medication: str
    dose: str
    frequency: str = ""
    status: str = ""
    prescriber: str = ""


@dataclass
class Symptom:
    symptom: str
    started: str  # as said, e.g. "since Tuesday"
    started_on: str | None  # resolved ISO date
    trigger: str = ""


@dataclass
class Source:
    id: str
    label: str
    kind: str
    date: str  # ISO effective date of the claims in this source
    text: str
    facts: list[MedFact] = field(default_factory=list)
    symptoms: list[Symptom] = field(default_factory=list)
    entities: list[dict[str, Any]] = field(default_factory=list)
    remembered: bool = False


@dataclass
class Call:
    sponsor: str  # SIE | Cognee | Keenable | Band | ElevenLabs
    op: str
    model: str
    ms: int
    note: str = ""
    at: float = field(default_factory=time.time)


class CaseState:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.room_id: str | None = None
        self.human_id: str | None = None
        self.documents: list[Document] = []
        self.sources: list[Source] = []
        self.conflicts: list[dict[str, Any]] = []
        self.confirmations: list[dict[str, Any]] = []
        self.timeline: list[dict[str, Any]] = []
        self.evidence: list[dict[str, Any]] = []
        self.brief: str | None = None
        self.calls: list[Call] = []
        self.messages: list[dict[str, Any]] = []
        self.graph: dict[str, Any] = {"nodes": [], "edges": []}
        self.agent_status: dict[str, str] = {}
        self.pending: dict[str, Any] | None = None
        self.answers: list[dict[str, Any]] = []
        self.version = 0

    # -- mutation helpers ------------------------------------------------------------------
    def touch(self) -> None:
        self.version += 1

    def add_document(self, name: str, path: str, kind: str, shared_by: str, note_date: str | None = None) -> Document:
        doc = Document(id=uuid.uuid4().hex[:8], name=name, path=path, kind=kind, shared_by=shared_by, note_date=note_date)
        self.documents.append(doc)
        self.touch()
        return doc

    def unread_documents(self) -> list[Document]:
        return [d for d in self.documents if not d.read]

    def unremembered_sources(self) -> list[Source]:
        return [s for s in self.sources if not s.remembered]

    def log_call(self, sponsor: str, op: str, model: str, ms: float, note: str = "") -> Call:
        call = Call(sponsor=sponsor, op=op, model=model, ms=int(ms), note=note)
        self.calls.append(call)
        self.touch()
        return call

    def set_status(self, agent: str, status: str) -> None:
        self.agent_status[agent] = status
        self.touch()

    # -- derived views ---------------------------------------------------------------------
    def medication_list(self) -> list[dict[str, Any]]:
        """Latest dose per medication, with its review status for the dashboard."""
        latest: dict[str, dict[str, Any]] = {}
        for src in sorted(self.sources, key=lambda s: s.date):
            for fact in src.facts:
                key = fact.medication.lower()
                entry = latest.setdefault(key, {"medication": key, "history": []})
                entry["history"].append({"dose": fact.dose, "frequency": fact.frequency, "status": fact.status,
                                         "source": src.label, "date": src.date})
                entry.update({"dose": fact.dose, "frequency": fact.frequency, "status": fact.status,
                              "source": src.label, "date": src.date})
        for conf in self.confirmations:
            key = conf["medication"].lower()
            if key in latest:
                latest[key].update({"dose": conf["dose"], "confirmed": conf})
        result = []
        for key, entry in latest.items():
            doses = {h["dose"] for h in entry["history"]}
            conflict = next((c for c in self.conflicts if key in c.get("about", "").lower()), None)
            if entry.get("status", "").lower().startswith("stop"):
                review = "stopped"
            elif entry.get("confirmed"):
                review = "confirmed"
            elif conflict or len(doses) > 1:
                review = "needs_confirmation"
            else:
                review = "ok"
            entry["review"] = review
            result.append(entry)
        order = {"needs_confirmation": 0, "confirmed": 1, "ok": 2, "stopped": 3}
        return sorted(result, key=lambda e: (order[e["review"]], e["medication"]))

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "room_id": self.room_id,
            "documents": [asdict(d) for d in self.documents],
            "sources": [{**asdict(s), "text": s.text[:1200]} for s in self.sources],
            "medications": self.medication_list(),
            "conflicts": self.conflicts,
            "confirmations": self.confirmations,
            "timeline": sorted(self.timeline, key=lambda e: (e["date"], e.get("order", 0))),
            "evidence": self.evidence,
            "brief": self.brief,
            "calls": [asdict(c) for c in self.calls[-60:]],
            "call_totals": self._call_totals(),
            "messages": self.messages,
            "graph": self.graph,
            "agent_status": self.agent_status,
            "pending": self.pending,
            "answers": self.answers,
        }

    def _call_totals(self) -> dict[str, Any]:
        totals: dict[str, Any] = {}
        for c in self.calls:
            t = totals.setdefault(c.sponsor, {"calls": 0, "ms": 0, "models": []})
            t["calls"] += 1
            t["ms"] += c.ms
            if c.model and c.model not in t["models"]:
                t["models"].append(c.model)
        return totals


STATE = CaseState()
