from __future__ import annotations

import json
import time
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.logger import logger
from app.db.models import (
    AutofillRun,
    AutofillSuggestion,
    FormInstance,
    FormInstanceField,
)
from app.services.autofill.autofill_decision_agent import LLMAutofillDecisionAgent
from app.services.autofill.contracts import AutofillDecision, CanonicalFormSchema
from app.services.autofill.feedback_learning_agent import LLMFeedbackLearningAgent
from app.services.autofill.field_understanding_agent import LLMFieldUnderstandingAgent
from app.services.autofill.form_parse_agent import LLMFormParseAgent
from app.core.config import settings
from app.services.autofill.memory_retrieval_agent import LLMMemoryRetrievalAgent
from app.services.autofill.rag_form_service import RagFormService


class AutofillOrchestrator:
    """Coordinate parse -> field understanding -> retrieval -> decision -> persistence."""

    def __init__(self) -> None:
        self.parse_agent = LLMFormParseAgent()
        self.field_agent = LLMFieldUnderstandingAgent()
        self.retrieval_agent = LLMMemoryRetrievalAgent()
        self.rag_form_service = RagFormService(self.retrieval_agent)
        self.decision_agent = LLMAutofillDecisionAgent()
        self.learning_agent = LLMFeedbackLearningAgent()

    async def parse_and_prepare_schema(
        self,
        *,
        db: Session,
        user_id: int,
        file_path: str,
        source_type: str,
        source_ref: str,
        original_filename: str,
    ) -> tuple[CanonicalFormSchema, int]:
        schema = await self.parse_agent.parse_uploaded_file(
            file_path=file_path,
            source_type=source_type,
            source_ref=source_ref,
            original_filename=original_filename,
        )
        schema = await self.field_agent.understand(schema)

        if settings.RAG_ENABLED and settings.RAG_INDEX_ON_UPLOAD:
            self.rag_form_service.index_uploaded_file(
                db,
                user_id=user_id,
                file_path=file_path,
                source_ref=source_ref or original_filename,
            )
            hints = self.rag_form_service.build_field_hints(db, user_id=user_id, fields=schema.fields)
            if hints:
                schema.metadata["rag_hints"] = hints

        form_instance_id = self._persist_form_instance(db=db, user_id=user_id, schema=schema)
        return schema, form_instance_id

    async def run_autofill(
        self,
        *,
        db: Session,
        user_id: int,
        form_instance_id: int,
    ) -> dict:
        form_instance = db.query(FormInstance).filter(
            FormInstance.id == form_instance_id,
            FormInstance.user_id == user_id,
        ).first()
        if form_instance is None:
            raise ValueError("Form instance not found")

        fields = db.query(FormInstanceField).filter(
            FormInstanceField.form_instance_id == form_instance_id
        ).order_by(FormInstanceField.display_order.asc(), FormInstanceField.id.asc()).all()

        start = time.time()
        run = AutofillRun(
            user_id=user_id,
            form_instance_id=form_instance_id,
            status="running",
            total_fields=len(fields),
            prefilled_fields=0,
            fallback_used=False,
            latency_ms=0,
            model_name="llm-agent-v1",
            notes="Autofill orchestrator run",
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        logger.info(
            f"Autofill run started id={run.id}, user={user_id}, "
            f"fields={len(fields)}, form_instance={form_instance_id}"
        )

        prefilled = 0
        fallback_used = False
        decisions_payload: list[dict] = []

        for field in fields:
            canonical = self._db_field_to_canonical(field)
            candidates = self.retrieval_agent.retrieve_for_field(
                db=db,
                user_id=user_id,
                field=canonical,
                top_k=5,
            )
            decision: AutofillDecision = await self.decision_agent.decide(
                field=canonical,
                candidates=candidates,
            )
            logger.info(
                f"Autofill field decision run={run.id}, field={canonical.field_key}, "
                f"has_value={bool(decision.value)}, fallback={decision.fallback_used}, "
                f"candidates={len(candidates)}"
            )
            if decision.value:
                prefilled += 1
            fallback_used = fallback_used or bool(decision.fallback_used)

            row = AutofillSuggestion(
                run_id=run.id,
                form_field_id=field.id,
                suggested_value=decision.value,
                confidence=decision.confidence,
                reason=decision.reason,
                fallback_used=bool(decision.fallback_used),
                source_trace_json=json.dumps(decision.source_trace, ensure_ascii=False),
            )
            db.add(row)

            decisions_payload.append(
                {
                    "field_id": field.id,
                    "field_key": canonical.field_key,
                    "label": canonical.label,
                    "suggested_value": decision.value,
                    "confidence": decision.confidence,
                    "reason": decision.reason,
                    "fallback_used": decision.fallback_used,
                }
            )

        latency_ms = int((time.time() - start) * 1000)
        run.status = "completed"
        run.prefilled_fields = prefilled
        run.fallback_used = fallback_used
        run.latency_ms = latency_ms
        run.created_at = run.created_at or datetime.utcnow()
        db.commit()

        logger.info(
            f"Autofill run completed id={run.id}, user={user_id}, "
            f"prefilled={prefilled}/{len(fields)}, latency_ms={latency_ms}"
        )
        return {
            "run_id": run.id,
            "form_instance_id": form_instance_id,
            "total_fields": len(fields),
            "prefilled_fields": prefilled,
            "fallback_used": fallback_used,
            "latency_ms": latency_ms,
            "suggestions": decisions_payload,
        }

    def _persist_form_instance(
        self,
        *,
        db: Session,
        user_id: int,
        schema: CanonicalFormSchema,
    ) -> int:
        instance = FormInstance(
            user_id=user_id,
            source_type=schema.source_type,
            source_ref=schema.source_ref,
            original_filename=schema.filename,
            schema_version=str(schema.metadata.get("schema_version") or "v1"),
            parse_status="parsed",
            parse_notes=str(schema.metadata.get("notes") or ""),
        )
        db.add(instance)
        db.commit()
        db.refresh(instance)

        fields: list[FormInstanceField] = []
        for idx, field in enumerate(schema.fields):
            fields.append(
                FormInstanceField(
                    form_instance_id=instance.id,
                    field_key=field.field_key,
                    field_label=field.label,
                    field_type=field.field_type,
                    aliases_json=json.dumps(field.aliases, ensure_ascii=False),
                    constraints_json=json.dumps(field.constraints, ensure_ascii=False),
                    display_order=idx,
                    is_required=bool(field.required),
                )
            )
        if fields:
            db.add_all(fields)
            db.commit()

        return int(instance.id)

    def _db_field_to_canonical(self, field: FormInstanceField):
        from app.services.autofill.contracts import CanonicalFormField

        aliases = []
        constraints = {}
        try:
            aliases = json.loads(field.aliases_json) if field.aliases_json else []
        except Exception:
            aliases = []
        try:
            constraints = json.loads(field.constraints_json) if field.constraints_json else {}
        except Exception:
            constraints = {}

        return CanonicalFormField(
            field_key=field.field_key,
            label=field.field_label,
            field_type=field.field_type or "text",
            required=bool(field.is_required),
            aliases=aliases if isinstance(aliases, list) else [],
            constraints=constraints if isinstance(constraints, dict) else {},
        )

