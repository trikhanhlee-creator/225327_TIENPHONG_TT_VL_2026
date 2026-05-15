from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.services.autofill.memory_service import UserMemoryService

router = APIRouter(prefix="/api/memory", tags=["memory"])


@router.post("/ingest-legacy")
async def ingest_legacy_memory(
    user_id: int | None = Query(None, description="Admin can ingest for another user"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    target_user_id = current_user.id
    if user_id is not None:
        if not current_user.is_admin and user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Không có quyền ingest dữ liệu user khác")
        target_user_id = int(user_id)

    ingested = UserMemoryService.ingest_legacy_entries(db=db, user_id=target_user_id)
    return JSONResponse(
        {
            "status": "success",
            "user_id": target_user_id,
            "ingested_rows": ingested,
            "message": "Đã đồng bộ dữ liệu lịch sử vào User Memory",
        }
    )


@router.get("")
async def list_memory(
    field_key: str | None = Query(None),
    top_k: int = Query(20),
    user_id: int | None = Query(None, description="Admin can read another user"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    target_user_id = current_user.id
    if user_id is not None:
        if not current_user.is_admin and user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Không có quyền xem dữ liệu user khác")
        target_user_id = int(user_id)

    items = UserMemoryService.list_memory(
        db=db,
        user_id=target_user_id,
        field_key=(field_key or "").strip() or None,
        top_k=min(max(top_k, 1), 200),
    )
    return JSONResponse(
        {
            "status": "success",
            "user_id": target_user_id,
            "field_key": field_key,
            "total": len(items),
            "items": items,
        }
    )

