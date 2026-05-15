from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.logger import logger
from app.db.models import Entry, Field, UserMemoryItem
from app.services.autofill.memory_chunk_indexer import MemoryChunkIndexer


class UserMemoryService:
    """Build and query unified user memory from legacy and new sources."""

    @staticmethod
    def ingest_legacy_entries(db: Session, user_id: int) -> int:
        rows = db.query(Entry.value, Entry.created_at, Field.field_name).join(
            Field, Entry.field_id == Field.id
        ).filter(
            Entry.user_id == user_id
        ).all()

        upserted = 0
        for value, created_at, field_name in rows:
            field_key = str(field_name or "").strip().lower().replace(" ", "_")
            val = str(value or "").strip()
            if not field_key or not val:
                continue

            existing = db.query(UserMemoryItem).filter(
                UserMemoryItem.user_id == user_id,
                UserMemoryItem.memory_type == "entry",
                UserMemoryItem.field_key == field_key,
                UserMemoryItem.value_text == val,
            ).first()

            if existing:
                existing.score = float(existing.score or 0.0) + 0.05
                existing.confidence = min(0.95, float(existing.confidence or 0.0) + 0.01)
            else:
                db.add(
                    UserMemoryItem(
                        user_id=user_id,
                        memory_type="entry",
                        field_key=field_key,
                        field_type="text",
                        value_text=val,
                        value_json=None,
                        source_ref="legacy_entry",
                        confidence=0.6,
                        score=0.2,
                        is_confirmed=False,
                        created_at=created_at,
                    )
                )
            upserted += 1

        db.commit()
        logger.info(f"UserMemoryService ingested {upserted} legacy rows for user={user_id}")
        try:
            MemoryChunkIndexer.reindex_all_memory_items_for_user(db, user_id)
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.warning(f"UserMemoryService RAG reindex after legacy ingest failed: {exc}")
        return upserted

    @staticmethod
    def list_memory(
        db: Session,
        user_id: int,
        field_key: str | None = None,
        top_k: int = 20,
    ) -> list[dict]:
        query = db.query(UserMemoryItem).filter(UserMemoryItem.user_id == user_id)
        if field_key:
            query = query.filter(UserMemoryItem.field_key == field_key)
        rows = query.order_by(
            UserMemoryItem.is_confirmed.desc(),
            UserMemoryItem.score.desc(),
            UserMemoryItem.updated_at.desc(),
        ).limit(max(top_k, 1)).all()

        return [
            {
                "id": row.id,
                "memory_type": row.memory_type,
                "field_key": row.field_key,
                "field_type": row.field_type,
                "value_text": row.value_text,
                "confidence": float(row.confidence or 0.0),
                "score": float(row.score or 0.0),
                "is_confirmed": bool(row.is_confirmed),
                "source_ref": row.source_ref,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
            for row in rows
        ]

    @staticmethod
    def upsert_memory_value(
        db: Session,
        *,
        user_id: int,
        field_key: str,
        value_text: str,
        memory_type: str = "entry",
        source_ref: str | None = None,
        confidence: float = 0.7,
        is_confirmed: bool = False,
    ) -> None:
        key = str(field_key or "").strip().lower().replace(" ", "_")
        value = str(value_text or "").strip()
        if not key or not value:
            return

        row = db.query(UserMemoryItem).filter(
            UserMemoryItem.user_id == user_id,
            UserMemoryItem.memory_type == memory_type,
            UserMemoryItem.field_key == key,
            UserMemoryItem.value_text == value,
        ).first()
        if row is None:
            row = UserMemoryItem(
                user_id=user_id,
                memory_type=memory_type,
                field_key=key,
                field_type="text",
                value_text=value,
                source_ref=source_ref,
                confidence=max(0.0, min(confidence, 1.0)),
                score=1.0 if is_confirmed else 0.3,
                is_confirmed=is_confirmed,
            )
            db.add(row)
        else:
            row.score = float(row.score or 0.0) + (0.8 if is_confirmed else 0.1)
            row.confidence = min(0.99, float(row.confidence or 0.0) + (0.08 if is_confirmed else 0.02))
            row.is_confirmed = row.is_confirmed or is_confirmed
            if source_ref:
                row.source_ref = source_ref

        db.flush()
        try:
            MemoryChunkIndexer.index_memory_item(db, row)
        except Exception as exc:
            logger.warning(f"UserMemoryService RAG index failed field={key}: {exc}")

