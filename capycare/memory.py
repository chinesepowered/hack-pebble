"""Cognee: the family's memory, running entirely on Superlinked SIE models.

Each source (discharge summary, pill bottle, phone call, voice note) is remembered as a dated
DataItem. Cognee's conflict review (improve(review_conflicts=True)) then finds facts that disagree
across dated sources, e.g. two different doses of the same medication. A family member's decision
is recorded with resolve_conflict(), and TEMPORAL recall answers "what changed before X?".
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from uuid import UUID

from . import config  # noqa: F401  (loads .env and drops AWS vars before cognee import)
import cognee
from cognee.context_global_variables import set_database_global_context_variables
from cognee.infrastructure.databases.graph import get_graph_engine
from cognee.modules.data.exceptions import DatasetNotFoundError
from cognee.modules.data.methods import get_authorized_existing_datasets
from cognee.modules.improve.config import get_improve_config
from cognee.modules.search.types import SearchType
from cognee.modules.users.methods import get_default_user
from cognee.tasks.ingestion.data_item import DataItem

from .state import STATE

log = logging.getLogger("capycare.memory")

EXTRACTION_PROMPT = (
    "You are building a medication record for one patient. Extract the patient, every medication "
    "(lowercase generic name, e.g. 'lisinopril'), every dose (e.g. '5 mg once daily'), every prescriber, "
    "every symptom, and every date. Connect the patient to each medication with takes. Connect each "
    "medication to its dose with has_dose. Connect each medication to its prescriber with prescribed_by. "
    "Connect the patient to each symptom with reports_symptom. Connect a dose change to its date with "
    "changed_on. Always reuse exactly the same names for the same patient, medication and person."
)
STRUCTURAL = {"DocumentChunk", "TextDocument", "TextSummary", "GlobalContextSummary", "EntityType",
              "EdgeType", "NodeSet", "Timestamp"}


def _plain(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return _plain(obj.model_dump())
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    if isinstance(obj, UUID):
        return str(obj)
    return obj


class FamilyMemory:
    def __init__(self, dataset: str = config.DATASET) -> None:
        self.dataset = dataset
        self.dataset_id: UUID | None = None
        self.lock = asyncio.Lock()

    async def _timed(self, op: str, coro, note: str = "") -> Any:
        started = time.perf_counter()
        result = await coro
        STATE.log_call("Cognee", op, "cognee 1.6 (LLM + embeddings on SIE)", (time.perf_counter() - started) * 1000, note)
        return result

    async def reset(self) -> None:
        async with self.lock:
            try:
                await cognee.forget(dataset=self.dataset)
            except DatasetNotFoundError:
                pass
            except Exception as exc:  # first run on a fresh install
                log.warning("forget(%s) failed: %r", self.dataset, exc)
            self.dataset_id = None

    async def remember(self, label: str, effective_date: str, text: str) -> None:
        key = get_improve_config().effective_date_key
        item = DataItem(text, label=label, external_metadata={key: effective_date}, literal_text=True)
        async with self.lock:
            result = await self._timed("remember", cognee.remember(
                item, dataset_name=self.dataset, self_improvement=False, custom_prompt=EXTRACTION_PROMPT),
                note=f"{label} (as of {effective_date})")
            self.dataset_id = UUID(str(result.dataset_id))

    async def review_conflicts(self) -> list[dict[str, Any]]:
        async with self.lock:
            await self._timed("improve", cognee.improve(dataset=self.dataset, review_conflicts=True),
                              note="conflict review across dated sources")
        return await self.conflicts()

    async def conflicts(self) -> list[dict[str, Any]]:
        if not self.dataset_id:
            return []
        async with self.lock:
            items = await cognee.list_conflicts(self.dataset_id)
        out = []
        for c in items:
            d = _plain(c)
            d["about"] = await self._node_name(d.get("about_id"))
            out.append(d)
        return out

    async def resolve(self, conflict_id: str, fact_id: str, reason: str) -> dict[str, Any]:
        async with self.lock:
            result = await self._timed("resolve_conflict", cognee.resolve_conflict(
                UUID(conflict_id), dataset_id=self.dataset_id, selected_fact_id=UUID(fact_id), reason=reason),
                note="family decision recorded")
        return _plain(result)

    async def ask(self, question: str, temporal: bool = False) -> str:
        query_type = SearchType.TEMPORAL if temporal else SearchType.GRAPH_COMPLETION
        async with self.lock:
            results = await self._timed("recall", cognee.recall(question, query_type=query_type, datasets=[self.dataset]),
                                        note=f"{query_type.name}: {question[:60]}")
        text = results[0].text if results else ""
        STATE.answers.append({"question": question, "type": query_type.name, "answer": text})
        return text

    async def _node_name(self, node_id: str | None) -> str:
        if not node_id:
            return ""
        graph = STATE.graph
        for node in graph.get("nodes", []):
            if node["id"] == str(node_id):
                return node["label"]
        return ""

    async def refresh_graph(self) -> dict[str, Any]:
        """Entity graph for the dashboard: people, medications, doses, symptoms, dates, conflicts."""
        user = await get_default_user()
        try:
            datasets = await get_authorized_existing_datasets(user=user, datasets=[self.dataset], permission_type="read")
        except Exception:
            return STATE.graph
        if not datasets:
            return STATE.graph
        ds = datasets[0]
        async with self.lock:
            async with set_database_global_context_variables(ds.id, ds.owner_id):
                nodes, edges = await (await get_graph_engine()).get_graph_data()
        keep: dict[str, dict[str, Any]] = {}
        for node_id, props in nodes:
            ntype = props.get("type")
            if ntype == "FactConflict":
                keep[str(node_id)] = {"id": str(node_id), "label": "conflict", "group": "conflict"}
            elif ntype == "Entity" and props.get("name"):
                keep[str(node_id)] = {"id": str(node_id), "label": props["name"], "group": "entity"}
            elif ntype == "Timestamp" and props.get("timestamp_str"):
                keep[str(node_id)] = {"id": str(node_id), "label": props["timestamp_str"], "group": "date"}
        types = {str(i): (p.get("name") or "") for i, p in nodes if p.get("type") == "EntityType"}
        for node_id, props in nodes:  # attach entity type (patient, medication, dose...) as the group
            if str(node_id) in keep and props.get("type") == "Entity":
                keep[str(node_id)]["kind"] = ""
        out_edges = []
        for src, dst, rel, _props in edges:
            s, d = str(src), str(dst)
            if rel == "is_a" and s in keep and d in types:
                keep[s]["kind"] = types[d]
            elif s in keep and d in keep:
                out_edges.append({"from": s, "to": d, "label": rel})
        STATE.graph = {"nodes": list(keep.values()), "edges": out_edges}
        STATE.touch()
        return STATE.graph


MEMORY = FamilyMemory()
