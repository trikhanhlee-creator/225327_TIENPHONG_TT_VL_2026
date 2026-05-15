from __future__ import annotations

from app.core.logger import logger
from app.services.autofill.contracts import AutofillDecision, CanonicalFormField, MemoryCandidate
from app.services.autofill.llm_client import LLMClient


class LLMAutofillDecisionAgent:
    """
    Final decision maker: choose best value from retrieved candidates.
    If LLM output is invalid, fallback to deterministic top candidate.
    """

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self._llm = llm_client or LLMClient()

    async def decide(
        self,
        *,
        field: CanonicalFormField,
        candidates: list[MemoryCandidate],
    ) -> AutofillDecision:
        if not candidates:
            return AutofillDecision(
                field_key=field.field_key,
                value="",
                confidence=0.0,
                reason="No memory candidates",
                source_trace=[],
                fallback_used=True,
            )

        prompt = (
            "Pick the best autofill value for field and return JSON "
            "{value,confidence,reason,source_index}. "
            f"field={field.field_key}, label={field.label}, type={field.field_type}, "
            f"candidates={[{'value': c.value, 'score': c.score, 'type': c.memory_type} for c in candidates]}"
        )
        result = await self._llm.complete_json(
            task_name="autofill_decision",
            prompt=prompt,
            fallback={},
        )

        if isinstance(result, dict):
            value = str(result.get("value") or "").strip()
            if value:
                confidence = float(result.get("confidence") or 0.0)
                reason = str(result.get("reason") or "LLM decision").strip()
                source_idx = int(result.get("source_index") or 0)
                if source_idx < 0 or source_idx >= len(candidates):
                    source_idx = 0
                source = candidates[source_idx]
                return AutofillDecision(
                    field_key=field.field_key,
                    value=value,
                    confidence=max(0.0, min(confidence, 1.0)),
                    reason=reason,
                    source_trace=[
                        {
                            "memory_type": source.memory_type,
                            "score": source.score,
                            "confidence": source.confidence,
                            "source_ref": source.source_ref,
                        }
                    ],
                    fallback_used=False,
                )

        # Deterministic fallback
        top = candidates[0]
        logger.info(f"LLMAutofillDecisionAgent fallback used for field={field.field_key}")
        return AutofillDecision(
            field_key=field.field_key,
            value=top.value,
            confidence=min(0.95, max(0.4, top.confidence)),
            reason="Deterministic top candidate fallback",
            source_trace=[
                {
                    "memory_type": top.memory_type,
                    "score": top.score,
                    "confidence": top.confidence,
                    "source_ref": top.source_ref,
                }
            ],
            fallback_used=True,
        )

