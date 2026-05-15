from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logger import logger
from app.db.models import Entry, Field, UserActivity, UserMemoryItem
from app.services.autofill.contracts import CanonicalFormField, MemoryCandidate
from app.services.autofill.embedding_service import embed_texts_sync
from app.services.autofill.memory_chunk_indexer import MemoryChunkIndexer


def _field_query_text(field: CanonicalFormField) -> str:
    parts: list[str] = [
        str(field.field_key or ""),
        str(field.label or ""),
        str(field.field_type or ""),
    ]
    if field.aliases:
        parts.extend(str(a) for a in field.aliases if a)
    return " ".join(p for p in parts if p).strip()


class LLMMemoryRetrievalAgent:
    """
    Retrieve candidates from unified memory + legacy history + optional semantic RAG chunks.
    LLM ranking is optional in later phases; hybrid deterministic + embedding retrieval.
    """

    def retrieve_for_field(
        self,
        *,
        db: Session,
        user_id: int,
        field: CanonicalFormField,
        top_k: int = 5,
    ) -> list[MemoryCandidate]:
        field_key = (field.field_key or "").strip()
        if not field_key:
            return []

        candidates: list[MemoryCandidate] = []

        # 1) Unified memory
        memory_rows = db.query(UserMemoryItem).filter(
            UserMemoryItem.user_id == user_id,
            UserMemoryItem.field_key == field_key,
        ).order_by(
            UserMemoryItem.is_confirmed.desc(),
            UserMemoryItem.score.desc(),
            UserMemoryItem.updated_at.desc(),
        ).limit(max(top_k * 3, 15)).all()
        for row in memory_rows:
            candidates.append(
                MemoryCandidate(
                    field_key=field_key,
                    value=str(row.value_text or "").strip(),
                    memory_type=row.memory_type,
                    score=float(row.score or 0.0),
                    confidence=float(row.confidence or 0.0),
                    source_ref=row.source_ref,
                    metadata={"confirmed": bool(row.is_confirmed)},
                )
            )

        # 2) Legacy entries fallback
        rows = db.query(Entry.value, Entry.created_at, Field.field_name).join(
            Field, Entry.field_id == Field.id
        ).filter(
            Entry.user_id == user_id
        ).all()

        stats: dict[str, dict] = defaultdict(lambda: {"freq": 0, "latest": None})
        for value, created_at, raw_name in rows:
            name = str(raw_name or "").strip().lower().replace(" ", "_")
            if name != field_key:
                continue
            val = str(value or "").strip()
            if not val:
                continue
            stats[val]["freq"] += 1
            if stats[val]["latest"] is None or (created_at and created_at > stats[val]["latest"]):
                stats[val]["latest"] = created_at

        now = datetime.utcnow()
        for val, detail in stats.items():
            latest = detail["latest"] or now
            recency_days = max((now - latest).days, 0)
            score = float(detail["freq"]) * 1.5 + max(0.0, 30.0 - recency_days) / 30.0
            candidates.append(
                MemoryCandidate(
                    field_key=field_key,
                    value=val,
                    memory_type="entry",
                    score=score,
                    confidence=min(0.95, 0.5 + (detail["freq"] * 0.1)),
                    source_ref="entries",
                    metadata={"frequency": detail["freq"]},
                )
            )

        # 3) Behavior hint (soft signal)
        activity_count = db.query(UserActivity).filter(
            UserActivity.user_id == user_id
        ).count()
        boost = 0.05 if activity_count > 20 else 0.0
        if boost:
            for item in candidates:
                item.confidence = min(0.99, item.confidence + boost)

        # 4) Semantic RAG chunks (same user only)
        if settings.RAG_ENABLED:
            qtext = _field_query_text(field)
            if qtext:
                qvec = embed_texts_sync([qtext])
                if qvec:
                    hits = MemoryChunkIndexer.semantic_search_for_user(
                        db,
                        user_id=user_id,
                        query_vector=qvec[0],
                        top_k=max(settings.RAG_SEMANTIC_TOP_K, 1),
                    )
                    for row, sim in hits:
                        raw = (row.chunk_text or "").strip()
                        if not raw:
                            continue
                        display = raw if len(raw) <= 800 else raw[:797] + "..."
                        candidates.append(
                            MemoryCandidate(
                                field_key=field_key,
                                value=display,
                                memory_type="rag",
                                score=float(sim) * 5.0,
                                confidence=min(0.99, max(0.0, float(sim))),
                                source_ref=row.source_ref,
                                metadata={
                                    "chunk_id": row.id,
                                    "semantic_similarity": float(sim),
                                    "memory_item_id": row.memory_item_id,
                                },
                            )
                        )

        candidates = [c for c in candidates if c.value]
        candidates.sort(key=lambda x: (x.score, x.confidence), reverse=True)
        dedup: list[MemoryCandidate] = []
        seen: set[str] = set()
        for item in candidates:
            key = item.value.lower()
            if key in seen:
                continue
            seen.add(key)
            dedup.append(item)
            if len(dedup) >= max(top_k, 1):
                break

        logger.info(
            f"LLMMemoryRetrievalAgent retrieved {len(dedup)} candidates "
            f"for user={user_id}, field={field_key} (rag_enabled={settings.RAG_ENABLED})"
        )
        return dedup

