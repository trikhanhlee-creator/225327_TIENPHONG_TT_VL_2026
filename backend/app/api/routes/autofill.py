from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.logger import logger
from app.db.models import (
    AutofillFeedback,
    AutofillRun,
    AutofillSuggestion,
    FormInstance,
    FormInstanceField,
    User,
)
from app.db.session import get_db
from app.services.autofill.memory_service import UserMemoryService
from app.services.autofill.orchestrator import AutofillOrchestrator

router = APIRouter(prefix="/api/autofill", tags=["autofill"])

UPLOAD_DIR = "uploads"
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)

orchestrator = AutofillOrchestrator()


@router.post("/upload-and-parse")
async def upload_and_parse_form(
    source_type: str = Query("word", description="word|excel|document"),
    source_ref: str = Query("", description="Template or session reference"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload any form file and build canonical schema via LLM agents."""
    source_type_norm = str(source_type or "word").strip().lower()
    if source_type_norm not in {"word", "excel", "document"}:
        raise HTTPException(status_code=400, detail="source_type không hợp lệ")

    filename = str(file.filename or "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="Thiếu tên file")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="File rỗng")

    file_path = os.path.join(
        UPLOAD_DIR,
        f"{current_user.id}_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}_{filename}",
    )
    with open(file_path, "wb") as out:
        out.write(content)

    effective_ref = str(source_ref or "").strip() or filename
    schema, form_instance_id = await orchestrator.parse_and_prepare_schema(
        db=db,
        user_id=current_user.id,
        file_path=file_path,
        source_type=source_type_norm,
        source_ref=effective_ref,
        original_filename=filename,
    )

    return JSONResponse(
        {
            "status": "success",
            "form_instance_id": form_instance_id,
            "source_type": schema.source_type,
            "source_ref": schema.source_ref,
            "filename": schema.filename,
            "fields_count": len(schema.fields),
            "fields": [
                {
                    "field_key": f.field_key,
                    "label": f.label,
                    "field_type": f.field_type,
                    "required": f.required,
                    "aliases": f.aliases,
                    "constraints": f.constraints,
                }
                for f in schema.fields
            ],
            "metadata": schema.metadata,
        }
    )


@router.post("/run/{form_instance_id}")
async def run_autofill(
    form_instance_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        payload = await orchestrator.run_autofill(
            db=db,
            user_id=current_user.id,
            form_instance_id=form_instance_id,
        )
        return JSONResponse({"status": "success", **payload})
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(f"Autofill run failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Lỗi chạy autofill: {exc}") from exc


@router.post("/run-by-source")
async def run_autofill_by_source(
    source_type: str = Query(..., description="word|excel|document"),
    source_ref: str = Query(..., description="Template ID hoặc session ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    source_type_norm = str(source_type or "").strip().lower()
    source_ref_text = str(source_ref or "").strip()
    if source_type_norm not in {"word", "excel", "document"}:
        raise HTTPException(status_code=400, detail="source_type không hợp lệ")
    if not source_ref_text:
        raise HTTPException(status_code=400, detail="source_ref không hợp lệ")

    instance = db.query(FormInstance).filter(
        FormInstance.user_id == current_user.id,
        FormInstance.source_type == source_type_norm,
        FormInstance.source_ref == source_ref_text,
    ).order_by(FormInstance.id.desc()).first()
    if instance is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy form_instance theo source_ref")

    try:
        payload = await orchestrator.run_autofill(
            db=db,
            user_id=current_user.id,
            form_instance_id=instance.id,
        )
        return JSONResponse(
            {
                "status": "success",
                "source_type": source_type_norm,
                "source_ref": source_ref_text,
                **payload,
            }
        )
    except Exception as exc:
        logger.error(f"Autofill run-by-source failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Lỗi chạy autofill theo source: {exc}") from exc


@router.get("/runs/{run_id}")
async def get_autofill_run(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    run = db.query(AutofillRun).filter(
        AutofillRun.id == run_id,
        AutofillRun.user_id == current_user.id,
    ).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Autofill run không tồn tại")

    suggestions = db.query(AutofillSuggestion).filter(
        AutofillSuggestion.run_id == run.id
    ).all()

    return JSONResponse(
        {
            "status": "success",
            "run": {
                "run_id": run.id,
                "form_instance_id": run.form_instance_id,
                "status": run.status,
                "total_fields": run.total_fields,
                "prefilled_fields": run.prefilled_fields,
                "fallback_used": run.fallback_used,
                "latency_ms": run.latency_ms,
                "model_name": run.model_name,
                "coverage": (float(run.prefilled_fields) / float(run.total_fields)) if run.total_fields else 0.0,
                "created_at": run.created_at.isoformat() if run.created_at else None,
            },
            "suggestions": [
                {
                    "id": row.id,
                    "form_field_id": row.form_field_id,
                    "suggested_value": row.suggested_value,
                    "confidence": float(row.confidence or 0.0),
                    "reason": row.reason,
                    "fallback_used": bool(row.fallback_used),
                    "source_trace_json": row.source_trace_json,
                }
                for row in suggestions
            ],
        }
    )


@router.get("/form-instances")
async def list_form_instances(
    source_type: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(FormInstance).filter(FormInstance.user_id == current_user.id)
    if source_type:
        query = query.filter(FormInstance.source_type == str(source_type).strip().lower())
    rows = query.order_by(FormInstance.created_at.desc()).limit(50).all()
    return JSONResponse(
        {
            "status": "success",
            "items": [
                {
                    "form_instance_id": row.id,
                    "source_type": row.source_type,
                    "source_ref": row.source_ref,
                    "filename": row.original_filename,
                    "parse_status": row.parse_status,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ],
        }
    )


@router.get("/form-instances/{form_instance_id}")
async def get_form_instance_detail(
    form_instance_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    instance = db.query(FormInstance).filter(
        FormInstance.id == form_instance_id,
        FormInstance.user_id == current_user.id,
    ).first()
    if instance is None:
        raise HTTPException(status_code=404, detail="Form instance không tồn tại")

    fields = db.query(FormInstanceField).filter(
        FormInstanceField.form_instance_id == instance.id
    ).order_by(FormInstanceField.display_order.asc(), FormInstanceField.id.asc()).all()
    return JSONResponse(
        {
            "status": "success",
            "item": {
                "form_instance_id": instance.id,
                "source_type": instance.source_type,
                "source_ref": instance.source_ref,
                "filename": instance.original_filename,
                "parse_status": instance.parse_status,
                "created_at": instance.created_at.isoformat() if instance.created_at else None,
                "field_count": len(fields),
                "fields": [
                    {
                        "field_id": f.id,
                        "field_key": f.field_key,
                        "field_label": f.field_label,
                        "field_type": f.field_type,
                        "aliases_json": f.aliases_json,
                        "constraints_json": f.constraints_json,
                        "is_required": f.is_required,
                    }
                    for f in fields
                ],
            },
        }
    )


@router.post("/review-confirm/{run_id}")
async def review_confirm_run(
    run_id: int,
    payload: dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    run = db.query(AutofillRun).filter(
        AutofillRun.id == run_id,
        AutofillRun.user_id == current_user.id,
    ).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Autofill run không tồn tại")

    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=400, detail="Thiếu items review")

    suggestion_map = {
        row.form_field_id: row
        for row in db.query(AutofillSuggestion).filter(AutofillSuggestion.run_id == run.id).all()
    }
    created = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            form_field_id = int(item.get("form_field_id") or 0)
        except Exception:
            form_field_id = 0
        if form_field_id <= 0:
            continue

        decision = str(item.get("decision") or "").strip().lower()
        if decision not in {"accepted", "edited", "rejected"}:
            continue

        suggestion = suggestion_map.get(form_field_id)
        suggested_value = suggestion.suggested_value if suggestion else None
        final_value = str(item.get("final_value") or "").strip()
        confidence = float(item.get("confidence") or (suggestion.confidence if suggestion else 0.0) or 0.0)
        note = str(item.get("note") or "").strip()

        feedback = AutofillFeedback(
            run_id=run.id,
            user_id=current_user.id,
            form_field_id=form_field_id,
            decision=decision,
            suggested_value=suggested_value,
            final_value=final_value or suggested_value,
            confidence=max(0.0, min(confidence, 1.0)),
            feedback_note=note,
        )
        db.add(feedback)

        field = db.query(FormInstanceField).filter(FormInstanceField.id == form_field_id).first()
        field_key = field.field_key if field else ""
        if field_key:
            final_text = final_value or suggested_value or ""
            if final_text:
                UserMemoryService.upsert_memory_value(
                    db=db,
                    user_id=current_user.id,
                    field_key=field_key,
                    value_text=final_text,
                    memory_type="confirmed",
                    source_ref=f"run:{run.id}",
                    confidence=max(0.5, min(confidence, 1.0)),
                    is_confirmed=decision in {"accepted", "edited"},
                )
            orchestrator.learning_agent.learn_from_feedback(
                db=db,
                user_id=current_user.id,
                field_key=field_key,
                suggested_value=suggested_value,
                final_value=final_value or suggested_value,
                decision=decision,
                source_ref=f"run:{run.id}",
            )
        created += 1

    if created:
        run.status = "reviewed"
        db.commit()

    return JSONResponse(
        {
            "status": "success",
            "run_id": run.id,
            "feedback_saved": created,
            "feedback_rate": (created / len(items)) if items else 0.0,
            "message": "Đã lưu review/confirm feedback",
        }
    )


@router.get("/metrics/overview")
async def get_autofill_metrics(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Basic demo metrics for autofill quality and fallback usage."""
    runs = db.query(AutofillRun).filter(
        AutofillRun.user_id == current_user.id
    ).order_by(AutofillRun.created_at.desc()).limit(200).all()
    feedbacks = db.query(AutofillFeedback).filter(
        AutofillFeedback.user_id == current_user.id
    ).order_by(AutofillFeedback.created_at.desc()).limit(500).all()

    total_runs = len(runs)
    avg_latency_ms = (
        sum(int(r.latency_ms or 0) for r in runs) / total_runs
        if total_runs
        else 0.0
    )
    avg_coverage = (
        sum((float(r.prefilled_fields) / float(r.total_fields)) if r.total_fields else 0.0 for r in runs) / total_runs
        if total_runs
        else 0.0
    )
    fallback_runs = sum(1 for r in runs if r.fallback_used)
    fallback_rate = (fallback_runs / total_runs) if total_runs else 0.0

    total_feedback = len(feedbacks)
    accepted = sum(1 for f in feedbacks if f.decision == "accepted")
    edited = sum(1 for f in feedbacks if f.decision == "edited")
    rejected = sum(1 for f in feedbacks if f.decision == "rejected")

    return JSONResponse(
        {
            "status": "success",
            "metrics": {
                "total_runs": total_runs,
                "avg_latency_ms": round(avg_latency_ms, 2),
                "avg_prefill_coverage": round(avg_coverage, 4),
                "fallback_rate": round(fallback_rate, 4),
                "total_feedback": total_feedback,
                "accepted_count": accepted,
                "edited_count": edited,
                "rejected_count": rejected,
                "acceptance_rate": round((accepted / total_feedback), 4) if total_feedback else 0.0,
            },
        }
    )

