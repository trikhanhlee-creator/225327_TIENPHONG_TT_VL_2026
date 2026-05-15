from __future__ import annotations

import json
from typing import Iterable

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logger import logger
from app.db.models import UserMemoryChunk, UserMemoryItem
from app.services.autofill.embedding_service import embed_texts_sync


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def split_text_chunks(text: str, *, limit: int, overlap: int) -> list[str]:
    t = (text or "").strip()
    if not t:
        return []
    if len(t) <= limit:
        return [t]
    chunks: list[str] = []
    start = 0
    while start < len(t):
        end = min(start + limit, len(t))
        piece = t[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(t):
            break
        start = max(end - overlap, start + 1)
    return chunks


def chunk_texts_for_memory_item(item: UserMemoryItem) -> list[str]:
    fk = (item.field_key or "").strip()
    val = str(item.value_text or "").strip()
    if not val:
        return []
    limit = max(64, settings.RAG_CHUNK_CHAR_LIMIT)
    overlap = max(0, min(settings.RAG_CHUNK_OVERLAP, limit // 2))
    raw_parts = split_text_chunks(val, limit=limit, overlap=overlap)
    prefixed: list[str] = []
    prefix = f"{fk}: " if fk else ""
    for part in raw_parts:
        chunk = f"{prefix}{part}".strip()
        if chunk:
            prefixed.append(chunk)
    return prefixed


class MemoryChunkIndexer:
    """Persist embeddings for UserMemoryItem rows (hybrid RAG recall)."""

    @staticmethod
    def delete_chunks_for_memory_item(db: Session, memory_item_id: int) -> None:
        db.query(UserMemoryChunk).filter(UserMemoryChunk.memory_item_id == memory_item_id).delete(
            synchronize_session=False
        )

    @staticmethod
    def index_memory_item(db: Session, item: UserMemoryItem) -> int:
        if not settings.RAG_ENABLED or item.id is None:
            return 0
        texts = chunk_texts_for_memory_item(item)
        if not texts:
            MemoryChunkIndexer.delete_chunks_for_memory_item(db, int(item.id))
            return 0

        vectors = embed_texts_sync(texts)
        if not vectors or len(vectors) != len(texts):
            logger.warning(
                f"MemoryChunkIndexer: embed failed or size mismatch for memory_item id={item.id}"
            )
            return 0

        MemoryChunkIndexer.delete_chunks_for_memory_item(db, int(item.id))
        dim = len(vectors[0])
        model = settings.RAG_EMBEDDING_MODEL
        for text, vec in zip(texts, vectors):
            row = UserMemoryChunk(
                user_id=item.user_id,
                memory_item_id=item.id,
                chunk_text=text[:8000],
                embedding_model=model,
                embedding_dim=dim,
                embedding_json=json.dumps(vec),
                source_ref=item.source_ref,
                field_key=item.field_key,
                extra_metadata_json=json.dumps(
                    {"memory_type": item.memory_type, "memory_item_id": item.id}, ensure_ascii=False
                ),
            )
            db.add(row)
        return len(texts)

    @staticmethod
    def reindex_all_memory_items_for_user(db: Session, user_id: int) -> int:
        if not settings.RAG_ENABLED:
            return 0
        items = (
            db.query(UserMemoryItem)
            .filter(UserMemoryItem.user_id == user_id)
            .order_by(UserMemoryItem.id.asc())
            .all()
        )
        db.query(UserMemoryChunk).filter(
            UserMemoryChunk.user_id == user_id,
            UserMemoryChunk.memory_item_id.isnot(None),
        ).delete(synchronize_session=False)
        db.flush()

        indexed_chunks = 0
        batch_texts: list[str] = []
        batch_meta: list[tuple[UserMemoryItem, str]] = []

        def flush_batch() -> None:
            nonlocal indexed_chunks, batch_texts, batch_meta
            if not batch_texts:
                return
            vectors = embed_texts_sync(batch_texts)
            if not vectors or len(vectors) != len(batch_texts):
                logger.warning("MemoryChunkIndexer reindex: batch embed failed, skipping batch")
                batch_texts = []
                batch_meta = []
                return
            dim = len(vectors[0])
            model = settings.RAG_EMBEDDING_MODEL
            for vec, (item, text) in zip(vectors, batch_meta):
                row = UserMemoryChunk(
                    user_id=item.user_id,
                    memory_item_id=item.id,
                    chunk_text=text[:8000],
                    embedding_model=model,
                    embedding_dim=dim,
                    embedding_json=json.dumps(vec),
                    source_ref=item.source_ref,
                    field_key=item.field_key,
                    extra_metadata_json=json.dumps(
                        {"memory_type": item.memory_type, "memory_item_id": item.id},
                        ensure_ascii=False,
                    ),
                )
                db.add(row)
                indexed_chunks += 1
            batch_texts = []
            batch_meta = []

        for item in items:
            for text in chunk_texts_for_memory_item(item):
                batch_texts.append(text)
                batch_meta.append((item, text))
                if len(batch_texts) >= settings.RAG_EMBED_BATCH_SIZE:
                    flush_batch()
        flush_batch()
        logger.info(f"MemoryChunkIndexer reindex user={user_id}, chunks_written={indexed_chunks}")
        return indexed_chunks

    @staticmethod
    def semantic_search_for_user(
        db: Session,
        *,
        user_id: int,
        query_vector: list[float],
        top_k: int,
    ) -> list[tuple[UserMemoryChunk, float]]:
        if not query_vector:
            return []
        max_scan = max(50, settings.RAG_MAX_CHUNKS_SCAN)
        rows = (
            db.query(UserMemoryChunk)
            .filter(UserMemoryChunk.user_id == user_id)
            .order_by(UserMemoryChunk.updated_at.desc())
            .limit(max_scan)
            .all()
        )
        scored: list[tuple[UserMemoryChunk, float]] = []
        for row in rows:
            try:
                vec = json.loads(row.embedding_json or "[]")
                if not isinstance(vec, list) or len(vec) != len(query_vector):
                    continue
                sim = cosine_similarity(query_vector, [float(x) for x in vec])
                scored.append((row, sim))
            except Exception:
                continue
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[: max(1, top_k)]


def optional_plaintext_chunks(
    *,
    text: str,
    source_ref: str,
    field_key: str | None = None,
) -> Iterable[tuple[str, str | None, str | None]]:
    """Yield (chunk_text, source_ref, field_key) for long pasted text (e.g. profile/CV snippets)."""
    if not settings.RAG_ENABLED or not (text or "").strip():
        return
    limit = max(64, settings.RAG_CHUNK_CHAR_LIMIT)
    overlap = max(0, min(settings.RAG_CHUNK_OVERLAP, limit // 2))
    fk = (field_key or "").strip().lower().replace(" ", "_") or None
    prefix = f"{fk}: " if fk else ""
    for part in split_text_chunks(text, limit=limit, overlap=overlap):
        chunk = f"{prefix}{part}".strip()
        if chunk:
            yield (chunk, source_ref, fk)


class StandaloneTextIndexer:
    """Index arbitrary user text as RAG chunks (memory_item_id NULL)."""

    @staticmethod
    def delete_by_source_ref(db: Session, user_id: int, source_ref: str) -> None:
        db.query(UserMemoryChunk).filter(
            UserMemoryChunk.user_id == user_id,
            UserMemoryChunk.memory_item_id.is_(None),
            UserMemoryChunk.source_ref == source_ref,
        ).delete(synchronize_session=False)

    @staticmethod
    def index_plaintext(
        db: Session,
        *,
        user_id: int,
        text: str,
        source_ref: str,
        field_key: str | None = None,
    ) -> int:
        if not settings.RAG_ENABLED:
            return 0
        chunks = list(optional_plaintext_chunks(text=text, source_ref=source_ref, field_key=field_key))
        if not chunks:
            return 0
        texts = [c[0] for c in chunks]
        vectors = embed_texts_sync(texts)
        if not vectors or len(vectors) != len(texts):
            return 0
        StandaloneTextIndexer.delete_by_source_ref(db, user_id, source_ref)
        dim = len(vectors[0])
        model = settings.RAG_EMBEDDING_MODEL
        for (chunk_text, sref, fk), vec in zip(chunks, vectors):
            row = UserMemoryChunk(
                user_id=user_id,
                memory_item_id=None,
                chunk_text=chunk_text[:8000],
                embedding_model=model,
                embedding_dim=dim,
                embedding_json=json.dumps(vec),
                source_ref=sref,
                field_key=fk,
                extra_metadata_json=json.dumps({"source_ref": sref}, ensure_ascii=False),
            )
            db.add(row)
        return len(chunks)
