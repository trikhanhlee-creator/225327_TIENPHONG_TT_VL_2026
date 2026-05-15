from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.logger import logger
from app.db.models import UserMemoryItem
from app.services.autofill.memory_chunk_indexer import MemoryChunkIndexer


class LLMFeedbackLearningAgent:
    """Update memory scores from user confirmation feedback."""

    def learn_from_feedback(
        self,
        *,
        db: Session,
        user_id: int,
        field_key: str,
        suggested_value: str | None,
        final_value: str | None,
        decision: str,
        source_ref: str | None = None,
    ) -> None:
        normalized_decision = str(decision or "").strip().lower()
        if normalized_decision not in {"accepted", "edited", "rejected"}:
            return

        norm_key = str(field_key or "").strip().lower().replace(" ", "_")
        final_text = str(final_value or "").strip()
        suggested_text = str(suggested_value or "").strip()

        # Reinforce accepted/edited final value as confirmed memory
        if final_text and normalized_decision in {"accepted", "edited"}:
            row = db.query(UserMemoryItem).filter(
                UserMemoryItem.user_id == user_id,
                UserMemoryItem.field_key == norm_key,
                UserMemoryItem.value_text == final_text,
            ).first()
            if row is None:
                row = UserMemoryItem(
                    user_id=user_id,
                    memory_type="confirmed",
                    field_key=norm_key,
                    field_type="text",
                    value_text=final_text,
                    source_ref=source_ref,
                    confidence=0.9,
                    score=1.0,
                    is_confirmed=True,
                )
                db.add(row)
            else:
                row.score = float(row.score or 0.0) + 0.8
                row.confidence = min(0.99, float(row.confidence or 0.0) + 0.1)
                row.is_confirmed = True

        # Penalize rejected suggestion
        if suggested_text and normalized_decision == "rejected":
            rejected_row = db.query(UserMemoryItem).filter(
                UserMemoryItem.user_id == user_id,
                UserMemoryItem.field_key == norm_key,
                UserMemoryItem.value_text == suggested_text,
            ).first()
            if rejected_row:
                rejected_row.score = max(0.0, float(rejected_row.score or 0.0) - 0.7)
                rejected_row.confidence = max(0.0, float(rejected_row.confidence or 0.0) - 0.15)

        db.flush()
        if final_text and normalized_decision in {"accepted", "edited"}:
            row = (
                db.query(UserMemoryItem)
                .filter(
                    UserMemoryItem.user_id == user_id,
                    UserMemoryItem.field_key == norm_key,
                    UserMemoryItem.value_text == final_text,
                )
                .first()
            )
            if row:
                try:
                    MemoryChunkIndexer.index_memory_item(db, row)
                except Exception as exc:
                    logger.warning(f"RAG reindex after feedback failed: {exc}")

        logger.info(
            f"LLMFeedbackLearningAgent learned feedback user={user_id}, "
            f"field={norm_key}, decision={normalized_decision}"
        )

