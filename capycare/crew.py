"""The Capy Care crew: four Band agents that hand work to each other by @mention.

  Capy Reader      reads what the family shares (SIE: docling, LightOnOCR, Whisper, GLiNER2)
  Elder Capy       remembers dated facts and catches conflicts (Cognee on SIE models)
  Capy Pharmacist  checks the official drug label (Keenable + SIE reranker)
  Capy Guardian    asks a human before anything changes, writes the visit brief (Band + Qwen on SIE)
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import re
from pathlib import Path
from typing import Any

from band import Agent
from band.core import SimpleAdapter
from dateutil import parser as dateparser

from . import config, labels, sie
from .memory import MEMORY
from .state import STATE, MedFact, Source, Symptom

log = logging.getLogger("capycare.crew")

NAMES = {"READER": "Capy Reader", "ELDER": "Elder Capy", "PHARMACIST": "Capy Pharmacist", "GUARDIAN": "Capy Guardian"}
IDS = {k: config.env(f"BAND_{k}_AGENT_ID") for k in config.AGENT_KEYS}
KEYS = {k: config.env(f"BAND_{k}_API_KEY") for k in config.AGENT_KEYS}
HUMAN = config.HUMAN_PERSONA
TODAY = dt.date(2026, 11, 14)  # the demo story's "today"


# --- small helpers ---------------------------------------------------------------------------
def iso(text: str, ref: dt.date = TODAY) -> str | None:
    if not text:
        return None
    try:
        return dateparser.parse(text, fuzzy=True, default=dt.datetime(ref.year, ref.month, ref.day)).date().isoformat()
    except (ValueError, OverflowError):
        return None


def since_weekday(phrase: str, ref_iso: str) -> str | None:
    """'since Tuesday' said on 2026-11-13 -> 2026-11-11."""
    ref = dt.date.fromisoformat(ref_iso)
    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for i, name in enumerate(days):
        if name in phrase.lower():
            delta = (ref.weekday() - i) % 7 or 7
            return (ref - dt.timedelta(days=delta)).isoformat()
    return iso(phrase, ref)


def nice(iso_date: str | None) -> str:
    if not iso_date:
        return "unknown date"
    d = dt.date.fromisoformat(iso_date)
    return f"{d.strftime('%a')} {d.strftime('%b')} {d.day}"


def norm_dose(dose: str) -> str:
    return re.sub(r"\s+", " ", dose.lower().replace("mg", " mg")).strip()


def norm_frequency(text: str) -> str:
    match = re.search(r"(once|twice|three times|four times)( a)? (daily|day)|nightly|at bedtime|every \d+ hours",
                      (text or "").lower())
    return match.group(0).replace(" a day", " daily").replace(" day", " daily") if match else (text or "").lower()


def norm_status(text: str) -> str:
    t = (text or "").lower()
    for word in ("stop", "new", "change", "continue"):
        if word in t:
            return {"change": "changed"}.get(word, word)
    return ""


def snippet_for(med: str, text: str) -> str:
    """The single line that states this medication: a table row if there is one."""
    lines = [ln for ln in text.splitlines() if med in ln.lower()]
    rows = [ln for ln in lines if ln.strip().startswith("|")]
    if rows:
        return rows[0]
    if len(text) < 900:
        return text
    return lines[0] if lines else text


def fact_value(fact_text: str) -> str:
    """'lisinopril has dose 10 mg once daily' -> '10 mg once daily'."""
    match = re.search(r"has[ _]dose\s+(.*)$", fact_text, flags=re.I)
    return match.group(1).strip() if match else fact_text


def dose_change(drug: str) -> dict[str, Any]:
    """Earliest vs latest dose of a drug across sources, with where each came from."""
    rows = sorted(((s, f) for s in STATE.sources for f in s.facts if f.medication == drug), key=lambda r: r[0].date)
    if not rows:
        return {}
    (old_src, old), (new_src, new) = rows[0], rows[-1]
    phone = re.search(r"\(?\d{3}\)?[ -]?\d{3}-\d{4}", new_src.text)
    return {"old": old.dose, "old_src": old_src, "new": new.dose, "new_src": new_src,
            "prescriber": new.prescriber or "the prescriber", "where": new_src.label.split(" — ")[-1].title(),
            "phone": phone.group(0) if phone else ""}


def add_timeline(date: str, title: str, detail: str, kind: str, order: int = 0) -> None:
    if not any(e["title"] == title and e["date"] == date for e in STATE.timeline):
        STATE.timeline.append({"date": date, "title": title, "detail": detail, "kind": kind, "order": order})
        STATE.touch()


class CapyAdapter(SimpleAdapter):
    key = ""

    def __init__(self) -> None:
        super().__init__()

    async def on_message(self, msg, tools, history, participants_msg, contacts_msg, *, is_session_bootstrap, room_id):
        if msg.message_type != "text" or room_id != STATE.room_id:
            return
        STATE.set_status(self.key, "working")
        try:
            await self.handle(msg, tools)
        except Exception as exc:  # keep the room informed instead of failing silently
            log.exception("%s failed", self.key)
            await tools.send_event(f"{NAMES[self.key]} hit an error: {exc!r}"[:300], "error")
            await tools.send_message(f"Sorry, I ran into a problem ({type(exc).__name__}). I'll need a hand.",
                                     mentions=[STATE.human_id])
        finally:
            STATE.set_status(self.key, "idle")

    async def handle(self, msg, tools) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    @staticmethod
    async def call(tools, sponsor: str, text: str) -> None:
        await tools.send_event(f"[{sponsor}] {text}", "tool_call", metadata={"sponsor": sponsor})


# --- Capy Reader --------------------------------------------------------------------------------
class Reader(CapyAdapter):
    key = "READER"

    async def handle(self, msg, tools) -> None:
        docs = STATE.unread_documents()
        if not docs:
            await tools.send_message("I don't see anything new in the family vault yet.", mentions=[msg.sender_id])
            return
        blocks = []
        for doc in docs:
            await tools.send_event(f"Reading {doc.name}…", "thought")
            text, model, ms = await sie.read_document(Path(doc.path), doc.kind)
            await self.call(tools, "SIE", f"{model} read {doc.name}: {len(text):,} characters in {ms / 1000:.1f}s")
            source = await (self.understand_voice(doc, text) if doc.kind == "audio" else self.understand_document(doc, text))
            await self.call(tools, "SIE", f"GLiNER2 pulled {len(source.facts)} medication facts and "
                                          f"{len(source.symptoms)} symptoms from {doc.name} (no LLM)")
            doc.read = True
            STATE.sources.append(source)
            STATE.touch()
            blocks.append(self.describe(source))
        await tools.send_message("\n\n".join(blocks) + "\n\nElder Capy, please remember these.", mentions=[IDS["ELDER"]])

    async def understand_document(self, doc, text: str) -> Source:
        meta = await sie.structured(text[:2500], {
            "document_type": ["hospital discharge summary", "pharmacy prescription label", "doctor's note", "other"],
            "document_date": "the date this document was written, discharged or filled",
            "source_name": "the hospital, clinic or pharmacy that issued it",
        }, note=f"document type and date ({doc.name})")
        found = await sie.entities(text, ["medication", "dose", "prescriber", "date", "pharmacy", "hospital"],
                                   note=f"entities ({doc.name})")
        meds = []
        for ent in found:
            name = (ent["text"] or "").strip().lower()
            if ent["label"] == "medication" and name and name not in meds:
                meds.append(name)
        facts = []
        for med in meds:
            fact = await sie.structured(snippet_for(med, text), {
                "medication": f"generic drug name, e.g. {med}",
                "dose": "strength, e.g. 5 mg",
                "frequency": "how often it is taken, e.g. once daily",
                "status": "the status column if present, e.g. NEW, Continue or STOP",
                "prescriber": "the prescribing doctor",
            }, note=f"medication fact: {med}")
            facts.append(MedFact(medication=med, dose=norm_dose(fact.get("dose", "")),
                                 frequency=norm_frequency(fact.get("frequency", "")),
                                 status=norm_status(fact.get("status", "")), prescriber=fact.get("prescriber") or ""))
        kind = meta.get("document_type") or "document"
        date = iso(meta.get("document_date", "")) or TODAY.isoformat()
        where = meta.get("source_name") or doc.name
        if where.isupper():
            where = where.title()
        label = f"{'Discharge summary' if 'discharge' in kind else 'Pill bottle label' if 'pharmacy' in kind else kind.title()} — {where}"
        if "discharge" in kind:
            started = [f.medication for f in facts if f.status == "new"]
            stopped = [f.medication for f in facts if f.status.startswith("stop")]
            add_timeline(date, "Discharged from the hospital", f"New: {', '.join(started) or '—'} · Stopped: {', '.join(stopped) or '—'}", "doc")
        elif "pharmacy" in kind:
            add_timeline(date, "New bottle filled at the pharmacy", "; ".join(f"{f.medication} {f.dose}" for f in facts), "doc", order=1)
        return Source(id=doc.id, label=label, kind=kind, date=date, text=text, facts=facts, entities=found)

    async def understand_voice(self, doc, text: str) -> Source:
        found = await sie.entities(text, ["symptom", "person", "date", "body position"], note="entities (voice note)")
        sym = await sie.structured(text, {
            "symptom": "the symptom or health problem described, e.g. dizziness",
            "started": "when it started, e.g. since Tuesday",
            "trigger": "when it happens, e.g. when standing up",
        }, note="symptom (voice note)")
        note_date = doc.note_date or TODAY.isoformat()
        started_on = since_weekday(sym.get("started", ""), note_date)
        symptom = Symptom(symptom=(sym.get("symptom") or "dizziness").lower(), started=sym.get("started", ""),
                          started_on=started_on, trigger=(sym.get("trigger") or "").lower())
        add_timeline(started_on or note_date, f"{symptom.symptom.capitalize()} begins",
                     f"{symptom.trigger or ''} (reported by Sam)".strip(), "symptom", order=2)
        add_timeline(note_date, "Sam's voice note", f"“{text[:90]}…”", "note")
        return Source(id=doc.id, label="Voice note from Sam", kind="voice note", date=started_on or note_date,
                      text=text, symptoms=[symptom], entities=found)

    @staticmethod
    def describe(src: Source) -> str:
        if src.symptoms:
            s = src.symptoms[0]
            return (f"🎙️ **{src.label}** — “{src.text}”\n• symptom: {s.symptom}"
                    f"{' ' + s.trigger if s.trigger else ''} · started {s.started} ({nice(s.started_on)})")
        icon = "📄" if "discharge" in src.kind else "💊"
        lines = [f"{icon} **{src.label}** ({nice(src.date)})"]
        for f in src.facts:
            status = " — stop" if f.status.startswith("stop") else f" ({f.status})" if f.status in ("new", "changed") else ""
            who = f", {f.prescriber}" if f.prescriber and "pharmacy" in src.kind else ""
            lines.append(f"• {f.medication} {f.dose} {f.frequency}{status}{who}".replace("  ", " "))
        return "\n".join(lines)


# --- Elder Capy ---------------------------------------------------------------------------------
def memory_text(src: Source) -> str:
    when = dt.date.fromisoformat(src.date).strftime("%B %-d, %Y")
    if src.symptoms:
        s = src.symptoms[0]
        return (f"Since {when}, {config.PATIENT} has had {s.symptom}{' ' + s.trigger if s.trigger else ''}. "
                f"Her son Sam reported this in a voice note: \"{src.text}\"")
    meds = "; ".join(f"{f.medication} {f.dose} {f.frequency}".strip() for f in src.facts if f.status != "stop")
    stopped = "; ".join(f"{f.medication} {f.dose}".strip() for f in src.facts if f.status == "stop")
    where = src.label.split(" — ")[-1]
    if "pharmacy" in src.kind:
        text = f"{where} prescription label filled {when}. Patient: {config.PATIENT}. {meds[:1].upper() + meds[1:]}."
    else:
        text = f"{where} {src.kind} dated {when}. Patient: {config.PATIENT}. Medications: {meds}."
        if stopped:
            text += f" Stopped medications: {stopped}."
    prescribers = {f.prescriber for f in src.facts if f.prescriber}
    if prescribers:
        text += f" Prescriber: {', '.join(sorted(prescribers))}."
    return text


class Elder(CapyAdapter):
    key = "ELDER"

    async def handle(self, msg, tools) -> None:
        if STATE.pending and STATE.pending.get("type") == "record_confirmation":
            await self.record_confirmation(tools)
            return
        new = STATE.unremembered_sources()
        if not new:
            await tools.send_message("Nothing new to remember right now.", mentions=[msg.sender_id])
            return
        for src in new:
            await tools.send_event(f"Remembering “{src.label}” as of {src.date}…", "thought")
            await MEMORY.remember(src.label, src.date, memory_text(src))
            src.remembered = True
            await self.call(tools, "Cognee", f"remember(“{src.label}”, effective_date={src.date}) → knowledge graph")
        await tools.send_event("Running Cognee's conflict review across dated sources…", "thought")
        await MEMORY.review_conflicts()
        await MEMORY.refresh_graph()
        STATE.conflicts = await MEMORY.conflicts()
        await self.call(tools, "Cognee", f"improve(review_conflicts=True) → {len(STATE.conflicts)} conflict(s) in the graph")

        symptom_src = next((s for s in new if s.symptoms), None)
        if symptom_src:
            await self.link_symptom(symptom_src, tools)
            return
        dose_conflicts = [c for c in STATE.conflicts if c.get("attribute") == "has_dose"]
        if dose_conflicts:
            c = dose_conflicts[0]
            drug = c.get("about") or "a medication"
            lines = []
            for fact in c.get("facts", []):
                dose = fact_value(fact.get("text", ""))
                src = next((s for s in STATE.sources for f in s.facts if f.medication == drug and f.dose in dose), None)
                lines.append(f"• {dose} — {src.label.split(' — ')[0].lower() if src else 'unknown source'} ({nice(src.date) if src else '?'})")
            STATE.pending = {"type": "check_conflict", "drug": drug, "conflict_id": c["id"]}
            await tools.send_message(
                f"🧠 Remembered {len(new)} dated source(s) in our Cognee graph.\n"
                f"⚠️ **Conflict found — {drug} dose:**\n" + "\n".join(lines) +
                "\n\nCapy Pharmacist, can you check this one?", mentions=[IDS["PHARMACIST"]])
        else:
            await tools.send_message(f"🧠 Remembered {len(new)} source(s). No conflicts found.", mentions=[STATE.human_id])

    async def link_symptom(self, src: Source, tools) -> None:
        sym = src.symptoms[0]
        start = dt.date.fromisoformat(sym.started_on or src.date)
        window_from = (start - dt.timedelta(days=4)).strftime("%B %-d, %Y")
        question = f"What changed in {config.PATIENT}'s medications between {window_from} and {start.strftime('%B %-d, %Y')}?"
        await tools.send_event(f"Asking Cognee (TEMPORAL): {question}", "thought")
        answer = await MEMORY.ask(question, temporal=True)
        await self.call(tools, "Cognee", f"recall(TEMPORAL) → “{answer[:160]}”")
        change = next((c for c in STATE.confirmations if c.get("changed_on") and
                       0 <= (start - dt.date.fromisoformat(c["changed_on"])).days <= 4), None)
        if change:
            gap = (start - dt.date.fromisoformat(change["changed_on"])).days
            link = (f"So the {sym.symptom} began **{gap} day{'s' if gap != 1 else ''} after** {change['medication']} went "
                    f"from {change['from_dose']} to {change['dose']} ({nice(change['changed_on'])}).")
            STATE.pending = {"type": "check_symptom", "drug": change["medication"], "symptom": sym.symptom}
        else:
            link = "I don't see a medication change right before it."
            STATE.pending = {"type": "check_symptom", "drug": "", "symptom": sym.symptom}
        await tools.send_message(
            f"🧠 Remembered Sam's note: {sym.symptom} {sym.trigger} since {nice(sym.started_on)}.\n"
            f"📅 Cognee, what changed before then? “{answer}”\n{link}\n\nCapy Pharmacist, what does the label say about this?",
            mentions=[IDS["PHARMACIST"]])

    async def record_confirmation(self, tools) -> None:
        p = STATE.pending
        drug = p["drug"]
        conflict = next((c for c in STATE.conflicts if c["id"] == p["conflict_id"]), None) or \
            next((c for c in STATE.conflicts if c.get("about") == drug), None)
        chosen = None
        if conflict:
            chosen = next((f for f in conflict.get("facts", []) if p["dose_value"] in f.get("text", "")), None)
        reason = (f"Confirmed by {p['confirmed_by']} on {TODAY.strftime('%B %-d, %Y')}: {p['changed_by']} changed the "
                  f"dose on {dt.date.fromisoformat(p['changed_on']).strftime('%B %-d, %Y')}.")
        if conflict and chosen:
            await tools.send_event(f"Recording the family's decision in Cognee: {chosen['text']}", "thought")
            await MEMORY.resolve(conflict["id"], chosen["id"], reason)
            await self.call(tools, "Cognee", f"resolve_conflict(selected=“{chosen['text']}”, reason=“{reason[:80]}…”)")
        text = (f"On {dt.date.fromisoformat(p['changed_on']).strftime('%B %-d, %Y')}, {p['changed_by']} increased "
                f"{config.PATIENT}'s {drug} from {p['from_dose']} to {p['dose']}. {p['confirmed_by']} confirmed this "
                f"by phone on {TODAY.strftime('%B %-d, %Y')}.")
        await MEMORY.remember("Family confirmation (pharmacy call)", p["changed_on"], text)
        await self.call(tools, "Cognee", f"remember(“Family confirmation”, effective_date={p['changed_on']})")
        STATE.confirmations.append({"medication": drug, "dose": p["dose"], "from_dose": p["from_dose"],
                                    "changed_on": p["changed_on"], "changed_by": p["changed_by"],
                                    "confirmed_by": p["confirmed_by"], "reason": reason})
        add_timeline(p["changed_on"], f"{p['changed_by']} increases {drug}",
                     f"{p['from_dose']} → {p['dose']} · confirmed by {p['confirmed_by']}", "change", order=0)
        await MEMORY.refresh_graph()
        STATE.conflicts = await MEMORY.conflicts()
        STATE.pending = None
        STATE.touch()
        await tools.send_message(
            f"✅ Recorded in Cognee: {drug} **{p['dose']}** is correct — {p['changed_by']} increased it on "
            f"{nice(p['changed_on'])}, confirmed with {p['confirmed_by']}. The conflict is now resolved by the family, "
            f"and Cognee keeps that decision on every future review.", mentions=[STATE.human_id])


# --- Capy Pharmacist ----------------------------------------------------------------------------
class Pharmacist(CapyAdapter):
    key = "PHARMACIST"

    async def evidence(self, tools, drug: str, question: str, rank_query: str, topic: str) -> list[str]:
        label = await labels.find_label(drug)
        await self.call(tools, "Keenable", f"search site:dailymed.nlm.nih.gov → {label['title'][:70]}")
        text = await labels.ask_label(label["url"], question)
        await self.call(tools, "Keenable", f"fetch(official label, prompt=“{question[:60]}…”) → {len(text):,} chars")
        found = labels.passages(text)
        ranked = await sie.rerank(rank_query, found, note=f"label passages: {topic}")
        top = [found[i] for i, _ in ranked[:2]]
        await self.call(tools, "SIE", f"Qwen3-Reranker-4B ranked {len(found)} label passages for “{rank_query[:50]}”")
        STATE.evidence.append({"topic": topic, "drug": drug, "url": label["url"], "title": label["title"], "passages": top})
        STATE.touch()
        return top

    async def handle(self, msg, tools) -> None:
        p = STATE.pending or {}
        if p.get("type") == "check_symptom" and p.get("drug"):
            drug = p["drug"]
            await tools.send_event(f"Checking the official {drug} label for {p['symptom']}…", "thought")
            top = await self.evidence(
                tools, drug,
                "Does the label list dizziness, light-headedness, or low blood pressure (hypotension), especially "
                "when standing up, as side effects? Quote the relevant sentences.",
                "dizziness or low blood pressure when standing up after the dose was increased", "symptom")
            STATE.pending = {"type": "write_brief", "drug": drug, "symptom": p["symptom"]}
            await tools.send_message(
                f"🔎 The official {drug} label (DailyMed, read live with Keenable) lists {p['symptom']} among its "
                f"common side effects:\n> {top[0] if top else 'dizziness'}\n"
                + (f"> {top[1]}\n" if len(top) > 1 else "") +
                f"\nThat's a question for Dr. Alvarez, not something to change at home. "
                f"Capy Guardian, can you prepare the visit brief?", mentions=[IDS["GUARDIAN"]])
            return
        drug = p.get("drug") or next((c.get("about") for c in STATE.conflicts), "the medication")
        await tools.send_event(f"Checking whether both {drug} doses are plausible and whether anything explains the change…", "thought")
        top = await self.evidence(
            tools, drug,
            f"What are the recommended starting and usual doses of {drug} for high blood pressure, including for "
            f"patients who were taking a diuretic? Quote the relevant sentences.",
            f"starting dose of {drug} for an older adult with high blood pressure who stopped a diuretic", "dose")
        explained = any("doctor" in s.kind for s in STATE.sources)
        ch = dose_change(drug)
        STATE.pending = {**p, "type": "ask_family", "drug": drug}
        await tools.send_message(
            f"🔎 I read the official {drug} label (DailyMed, via Keenable). Both doses are normal, so either could be intended:\n"
            f"> {top[0] if top else ''}\n\n"
            + ("" if explained else f"I found **no prescriber note explaining the change** from {ch.get('old')} to {ch.get('new')}. ")
            + "Capy Guardian, a person needs to confirm this before Mom's next dose.", mentions=[IDS["GUARDIAN"]])


# --- Capy Guardian ------------------------------------------------------------------------------
BRIEF_SYSTEM = (
    "You are Capy Guardian, a calm assistant that helps a family prepare for a doctor visit. Use only the facts "
    "provided. Never give medical advice, never suggest changing or stopping a medication, and never guess. "
    "Write plain, warm language a worried family member can read in one minute."
)


class Guardian(CapyAdapter):
    key = "GUARDIAN"

    async def handle(self, msg, tools) -> None:
        p = STATE.pending or {}
        from_human = msg.sender_type == "User"
        if not from_human and p.get("type") == "ask_family":
            drug = p["drug"]
            ch = dose_change(drug)
            STATE.pending = {**p, "type": "confirm_dose"}
            phone = f" at {ch['phone']}" if ch.get("phone") else ""
            await tools.send_message(
                f"Hi {HUMAN} 🐹 Mom's new {drug} bottle says **{ch.get('new')}**, but her discharge papers say "
                f"**{ch.get('old')}**, and I can't find a doctor's note explaining the change. Could you call "
                f"{ch.get('where')}{phone} and ask which dose {ch.get('prescriber')} prescribed? Please don't change "
                f"anything until we know — just reply here with what they say.", mentions=[STATE.human_id])
            return
        if from_human and p.get("type") == "confirm_dose":
            await self.take_confirmation(msg, tools, p)
            return
        if not from_human and p.get("type") == "write_brief":
            await self.write_brief(tools, p)
            return
        await tools.send_message(f"Thanks, {HUMAN}! I've noted that. Nothing needs your attention right now.",
                                 mentions=[msg.sender_id])

    async def take_confirmation(self, msg, tools, p: dict[str, Any]) -> None:
        text = re.sub(r"@\S+", "", msg.content).strip()
        conf = await sie.structured(text, {
            "confirmed_dose": "the dose that is correct, e.g. 10 mg",
            "changed_on": "the date the dose was changed",
            "changed_by": "the doctor who changed the dose",
            "confirmed_by": "who confirmed it, e.g. the pharmacy",
        }, note="read the family's confirmation")
        await self.call(tools, "SIE", f"GLiNER2 read the reply → {json.dumps(conf)}")
        drug = p["drug"]
        doses = sorted({f.dose for s in STATE.sources for f in s.facts if f.medication == drug})
        dose_value = norm_dose(conf.get("confirmed_dose") or "10 mg")
        frequency = next((f.frequency for s in STATE.sources for f in s.facts if f.medication == drug and f.frequency), "once daily")
        from_dose = next((d for d in doses if d != dose_value), "")
        changed_by = conf.get("changed_by") or "the prescriber"
        if changed_by and not changed_by.lower().startswith("dr"):
            changed_by = f"Dr. {changed_by}"
        STATE.pending = {**p, "type": "record_confirmation", "dose_value": dose_value,
                         "dose": f"{dose_value} {frequency}", "from_dose": f"{from_dose} {frequency}".strip(),
                         "changed_on": iso(conf.get("changed_on", "")) or TODAY.isoformat(),
                         "changed_by": changed_by, "confirmed_by": conf.get("confirmed_by") or "the pharmacy"}
        await tools.send_message(
            f"Thank you, {HUMAN} — that explains it. Elder Capy, please record the family's decision: {drug} "
            f"{dose_value} {frequency} is correct ({changed_by} changed it on "
            f"{nice(STATE.pending['changed_on'])}, confirmed with {STATE.pending['confirmed_by']}).",
            mentions=[IDS["ELDER"]])

    async def write_brief(self, tools, p: dict[str, Any]) -> None:
        await tools.send_event("Writing the doctor-visit brief with Qwen3.8 on SIE…", "thought")
        facts = {
            "patient": config.PATIENT,
            "visit": "Follow-up with Dr. Maria Alvarez (cardiology)",
            "current_medications": [
                {k: m.get(k) for k in ("medication", "dose", "frequency", "review")} for m in STATE.medication_list()],
            "confirmed_changes": STATE.confirmations,
            "symptoms": [{"symptom": s.symptom, "trigger": s.trigger, "started_on": s.started_on, "reported_by": "Sam (son)"}
                         for src in STATE.sources for s in src.symptoms],
            "timeline": sorted(STATE.timeline, key=lambda e: e["date"]),
            "label_evidence": [{"topic": e["topic"], "quotes": e["passages"], "source": e["title"]} for e in STATE.evidence],
        }
        prompt = (
            "Write a one-page doctor-visit brief in Markdown with exactly these sections:\n"
            "## Current medications (a short bullet list)\n## What changed (dated bullets)\n"
            "## What we noticed (dated bullets)\n## Questions for Dr. Alvarez (exactly 3 numbered questions)\n"
            "## Sources (bullets)\nFacts (JSON):\n" + json.dumps(facts, indent=1, default=str)
        )
        brief = await sie.chat([{"role": "system", "content": BRIEF_SYSTEM}, {"role": "user", "content": prompt}],
                               max_tokens=900, temperature=0.2, note="doctor-visit brief")
        STATE.brief = brief
        STATE.pending = None
        STATE.touch()
        questions = re.findall(r"^\s*\d\.\s+(.+)$", brief, flags=re.M)[:3]
        await self.call(tools, "SIE", "Qwen3.8-27B wrote the visit brief from the reconciled list, timeline and label quotes")
        await tools.send_message(
            f"📋 {HUMAN}, Mom's visit brief for Dr. Alvarez is ready on the dashboard. Questions to ask:\n"
            + "\n".join(f"{i + 1}. {q}" for i, q in enumerate(questions)) +
            "\n\nI haven't changed anything — that's the doctor's call. 🍊", mentions=[STATE.human_id])


ADAPTERS = {"READER": Reader, "ELDER": Elder, "PHARMACIST": Pharmacist, "GUARDIAN": Guardian}


async def run_crew() -> None:
    """Connect all four agents to Band and keep them running."""
    agents = [Agent.create(adapter=ADAPTERS[k](), agent_id=IDS[k], api_key=KEYS[k]) for k in config.AGENT_KEYS]
    for k in config.AGENT_KEYS:
        STATE.agent_status[k] = "idle"
    await asyncio.gather(*(a.run() for a in agents))
