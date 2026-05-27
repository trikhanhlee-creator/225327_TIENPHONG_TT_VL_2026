from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logger import logger
from app.db.models import Entry, Field, UserActivity, UserMemoryItem
from app.services.autofill.contracts import CanonicalFormField, MemoryCandidate
from app.services.autofill.embedding_service import embed_texts_sync
from app.services.autofill.memory_chunk_indexer import MemoryChunkIndexer
from app.services.suggestion_value_filters import is_valid_field_suggestion_value

# Retrieval tiers (lower = higher priority in final ranking)
TIER_CONFIRMED = 0
TIER_MEMORY = 1
TIER_RAG_FIELD = 2
TIER_RAG_HIGH = 3
TIER_ENTRY = 4
TIER_RAG_WEAK = 5

_TIER_BASE_SCORE = {
    TIER_CONFIRMED: 1000.0,
    TIER_MEMORY: 820.0,
    TIER_RAG_FIELD: 680.0,
    TIER_RAG_HIGH: 540.0,
    TIER_ENTRY: 360.0,
    TIER_RAG_WEAK: 220.0,
}


def _field_query_text(field: CanonicalFormField) -> str:
    parts: list[str] = [
        str(field.field_key or ""),
        str(field.label or ""),
        str(field.field_type or ""),
    ]
    if field.aliases:
        parts.extend(str(a) for a in field.aliases if a)
    return " ".join(p for p in parts if p).strip()


def _normalize_field_key(raw: str) -> str:
    return str(raw or "").strip().lower().replace(" ", "_")


def _rag_min_similarity() -> float:
    return max(0.0, min(1.0, float(getattr(settings, "RAG_MIN_SIMILARITY", 0.55))))


def _rag_high_similarity() -> float:
    high = float(getattr(settings, "RAG_HIGH_SIMILARITY", 0.72))
    return max(_rag_min_similarity(), min(1.0, high))


def _extract_rag_value(chunk_text: str, field_key: str) -> str:
    """Prefer structured 'field_key: value' over raw chunk text for autofill."""
    text = (chunk_text or "").strip()
    if not text:
        return ""

    fk = _normalize_field_key(field_key)
    for line in text.splitlines():
        line = line.strip()
        if ":" not in line:
            continue
        key_part, _, value_part = line.partition(":")
        if _normalize_field_key(key_part) == fk:
            val = value_part.strip()
            if val:
                return val

    if fk and text.lower().startswith(f"{fk}:"):
        return text.split(":", 1)[1].strip()

    if len(text) <= 80 and not re.search(r"\n\s*\n", text) and is_valid_field_suggestion_value(text):
        return text
    return ""


def _length_penalty(value: str) -> float:
    n = len(value or "")
    if n <= 120:
        return 0.0
    if n <= 400:
        return (n - 120) * 0.015
    return 4.2 + (n - 400) * 0.04


def _composite_score(*, tier: int, confidence: float, score: float, similarity: float = 0.0, value: str = "") -> float:
    base = _TIER_BASE_SCORE.get(tier, 0.0)
    return base + (confidence * 50.0) + (score * 8.0) + (similarity * 120.0) - _length_penalty(value)


def _rank_key(candidate: MemoryCandidate) -> tuple:
    tier = int(candidate.metadata.get("tier", TIER_RAG_WEAK))
    composite = float(candidate.metadata.get("composite_score", candidate.score))
    return (tier, -composite, -candidate.confidence, len(candidate.value or ""))


class LLMMemoryRetrievalAgent:
    """
    Hybrid retrieval ranked by source quality:
    confirmed memory → structured memory → RAG (field match / high sim) → legacy history → weak RAG.
    """

    def retrieve_for_field(
        self,
        *,
        db: Session,
        user_id: int,
        field: CanonicalFormField,
        top_k: int = 5,
    ) -> list[MemoryCandidate]:
        field_key = _normalize_field_key(field.field_key or "")
        if not field_key:
            return []

        candidates: list[MemoryCandidate] = []
        min_sim = _rag_min_similarity()
        high_sim = _rag_high_similarity()

        # 1) Structured memory (exact field_key) — highest trust
        memory_rows = (
            db.query(UserMemoryItem)
            .filter(
                UserMemoryItem.user_id == user_id,
                UserMemoryItem.field_key == field_key,
            )
            .order_by(
                UserMemoryItem.is_confirmed.desc(),
                UserMemoryItem.score.desc(),
                UserMemoryItem.updated_at.desc(),
            )
            .limit(max(top_k * 3, 15))
            .all()
        )
        for row in memory_rows:
            value = str(row.value_text or "").strip()
            if not value:
                continue
            confirmed = bool(row.is_confirmed)
            tier = TIER_CONFIRMED if confirmed else TIER_MEMORY
            confidence = float(row.confidence or 0.0)
            if confirmed:
                confidence = max(confidence, 0.92)
            score = float(row.score or 0.0)
            composite = _composite_score(
                tier=tier,
                confidence=confidence,
                score=score,
                value=value,
            )
            candidates.append(
                MemoryCandidate(
                    field_key=field_key,
                    value=value,
                    memory_type=row.memory_type,
                    score=composite,
                    confidence=confidence,
                    source_ref=row.source_ref,
                    metadata={
                        "tier": tier,
                        "composite_score": composite,
                        "confirmed": confirmed,
                        "source": "user_memory_item",
                    },
                )
            )

        strong_memory_count = sum(
            1 for c in candidates if c.metadata.get("tier", 99) <= TIER_MEMORY and c.confidence >= 0.85
        )
        skip_rag = bool(getattr(settings, "RAG_SKIP_IF_STRONG_MEMORY", False)) and strong_memory_count >= max(top_k, 1)

        # 2) Semantic RAG — before legacy so cross-field hints can surface, but ranked below memory
        if settings.RAG_ENABLED and not skip_rag:
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
                        if float(sim) < min_sim:
                            continue
                        raw = (row.chunk_text or "").strip()
                        if not raw:
                            continue

                        chunk_fk = _normalize_field_key(getattr(row, "field_key", None) or "")
                        extracted = _extract_rag_value(raw, field_key)
                        value = extracted or (raw if len(raw) <= 80 else "")
                        if not value or not is_valid_field_suggestion_value(value):
                            continue

                        field_match = bool(chunk_fk and chunk_fk == field_key)
                        if field_match:
                            tier = TIER_RAG_FIELD
                        elif float(sim) >= high_sim:
                            tier = TIER_RAG_HIGH
                        else:
                            tier = TIER_RAG_WEAK

                        confidence = min(0.99, max(0.0, float(sim)))
                        if field_match:
                            confidence = min(0.99, confidence + 0.08)
                        if extracted:
                            confidence = min(0.99, confidence + 0.05)

                        composite = _composite_score(
                            tier=tier,
                            confidence=confidence,
                            score=float(sim) * 5.0,
                            similarity=float(sim),
                            value=value,
                        )
                        candidates.append(
                            MemoryCandidate(
                                field_key=field_key,
                                value=value,
                                memory_type="rag",
                                score=composite,
                                confidence=confidence,
                                source_ref=row.source_ref,
                                metadata={
                                    "tier": tier,
                                    "composite_score": composite,
                                    "chunk_id": row.id,
                                    "semantic_similarity": float(sim),
                                    "memory_item_id": row.memory_item_id,
                                    "field_match": field_match,
                                    "extracted": bool(extracted),
                                    "source": "semantic_rag",
                                },
                            )
                        )

        # 3) Legacy entries — noisy; lower priority than structured memory & strong RAG
        rows = (
            db.query(Entry.value, Entry.created_at, Field.field_name)
            .join(Field, Entry.field_id == Field.id)
            .filter(Entry.user_id == user_id)
            .all()
        )
        stats: dict[str, dict] = defaultdict(lambda: {"freq": 0, "latest": None})
        for value, created_at, raw_name in rows:
            name = _normalize_field_key(raw_name)
            if name != field_key:
                continue
            val = str(value or "").strip()
            if not val:
                continue
            stats[val]["freq"] += 1
            if stats[val]["latest"] is None or (created_at and created_at > stats[val]["latest"]):
                stats[val]["latest"] = created_at

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for val, detail in stats.items():
            latest = detail["latest"] or now
            recency_days = max((now - latest).days, 0) if latest else 0
            freq_score = float(detail["freq"]) * 1.5 + max(0.0, 30.0 - recency_days) / 30.0
            confidence = min(0.9, 0.45 + (detail["freq"] * 0.08))
            composite = _composite_score(
                tier=TIER_ENTRY,
                confidence=confidence,
                score=freq_score,
                value=val,
            )
            candidates.append(
                MemoryCandidate(
                    field_key=field_key,
                    value=val,
                    memory_type="entry",
                    score=composite,
                    confidence=confidence,
                    source_ref="entries",
                    metadata={
                        "tier": TIER_ENTRY,
                        "composite_score": composite,
                        "frequency": detail["freq"],
                        "source": "legacy_entry",
                    },
                )
            )

        # 4) Activity boost (soft signal on confidence only)
        activity_count = db.query(UserActivity).filter(UserActivity.user_id == user_id).count()
        boost = 0.04 if activity_count > 20 else 0.0
        if boost:
            for item in candidates:
                if item.metadata.get("tier", 99) <= TIER_MEMORY:
                    item.confidence = min(0.99, item.confidence + boost)

        candidates = [c for c in candidates if c.value]
        candidates.sort(key=_rank_key)

        dedup: list[MemoryCandidate] = []
        seen: set[str] = set()
        for item in candidates:
            norm = item.value.strip().lower()
            if norm in seen:
                continue
            seen.add(norm)
            dedup.append(item)
            if len(dedup) >= max(top_k, 1):
                break

        logger.info(
            f"LLMMemoryRetrievalAgent retrieved {len(dedup)} candidates "
            f"for user={user_id}, field={field_key} "
            f"(rag_enabled={settings.RAG_ENABLED}, skip_rag={skip_rag}, "
            f"tiers={[c.metadata.get('tier') for c in dedup]})"
        )
        return dedup
