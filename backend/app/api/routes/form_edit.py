"""
API routes cho chỉnh sửa form fields bằng AI
- Lấy AI suggestions để cải thiện giá trị field
- Thực hiện edits dựa trên AI recommendations
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional, List
from pydantic import BaseModel

from app.db.session import get_db
from app.db.models import User
from app.core.logger import logger
from app.core.auth import get_current_user
from app.services.ai_composer_service import AIComposerService
from app.api.routes.word import persist_word_fields_for_learning, resolve_effective_user_id
from app.services.autofill.memory_service import UserMemoryService

# Khởi tạo AI service
ai_service = AIComposerService()

# Router
router = APIRouter(
    prefix="/api/form-edit",
    tags=["form-edit"],
    responses={404: {"description": "Not found"}},
)


# ============ Pydantic Models ============

class FieldEditRequest(BaseModel):
    """Schema cho request AI suggestions để edit form field"""
    field_name: str
    current_value: str
    field_type: Optional[str] = "text"  # text, email, phone, address, etc.
    context: Optional[str] = None  # thêm context (ví dụ: tên công ty)
    max_suggestions: int = 3


class EditSuggestion(BaseModel):
    """Schema cho response suggestions"""
    text: str
    confidence: float
    reason: Optional[str] = None


class FieldEditResponse(BaseModel):
    """Schema cho response"""
    success: bool
    field_name: str
    original_value: str
    suggestions: List[EditSuggestion]


# ============ API Endpoints ============

@router.post("/suggestions", response_model=FieldEditResponse)
async def get_field_edit_suggestions(
    request: FieldEditRequest,
    db: Session = Depends(get_db)
):
    """
    Lấy AI suggestions để cải thiện giá trị form field
    
    Example:
    {
        "field_name": "Tên công ty",
        "current_value": "abc xyz",
        "field_type": "text",
        "max_suggestions": 3
    }
    
    Returns:
    {
        "success": true,
        "field_name": "Tên công ty",
        "original_value": "abc xyz",
        "suggestions": [
            {"text": "ABC XYZ Corporation", "confidence": 0.95},
            {"text": "ABC XYZ Co., Ltd.", "confidence": 0.85},
            {"text": "ABC XYZ Enterprises", "confidence": 0.80}
        ]
    }
    """
    try:
        logger.info(f"Getting edit suggestions for field: {request.field_name}")
        
        # Tạo prompt để AI cải thiện giá trị field
        prompt_context = f"""
Công việc: Cải thiện/chuẩn hóa giá trị form field
Tên field: {request.field_name}
Loại field: {request.field_type}
Giá trị hiện tại: "{request.current_value}"
{"Ngữ cảnh bổ sung: " + request.context if request.context else ""}

Yêu cầu:
1. Tạo 2-3 gợi ý để cải thiện/chuẩn hóa giá trị này
2. Giữ nguyên ý nghĩa nhưng chuẩn hóa hơn
3. Mỗi gợi ý cần:
   - Có lý do tại sao cải thiện
   - Fit với kiểu field ({request.field_type})
4. Trả lại JSON format:
[
    {{"text": "gợi ý 1", "confidence": 0.95, "reason": "Lý do"}},
    {{"text": "gợi ý 2", "confidence": 0.85, "reason": "Lý do"}}
]
"""
        
        # Gọi AI service để lấy suggestions
        ai_suggestions = await ai_service.get_text_suggestions(
            context=prompt_context,
            max_suggestions=request.max_suggestions,
            mode="rewrite",
            original_text=request.current_value
        )
        
        # Parse suggestions
        suggestions = []
        if ai_suggestions:
            for suggestion in ai_suggestions:
                try:
                    suggestions.append(EditSuggestion(
                        text=suggestion.get("text", ""),
                        confidence=min(suggestion.get("confidence", 0.8), 1.0),
                        reason=suggestion.get("reason", "")
                    ))
                except Exception as e:
                    logger.warning(f"Failed to parse suggestion: {e}")
        
        logger.info(f"Generated {len(suggestions)} suggestions for {request.field_name}")
        
        return FieldEditResponse(
            success=True,
            field_name=request.field_name,
            original_value=request.current_value,
            suggestions=suggestions
        )
        
    except Exception as e:
        logger.error(f"Error getting edit suggestions: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get edit suggestions: {str(e)}"
        )


class ApplySuggestionRequest(BaseModel):
    """Schema cho apply suggestion request"""
    field_name: str
    """Tên hiển thị / nhãn trường (cho log)."""

    field_key: Optional[str] = None
    """Khóa trường nội bộ Word (`data-field-name`), để map Field.id và memory."""

    template_id: Optional[int] = None
    """Template Word — bắt buộc để đồng bộ Entry + RAG."""

    original_value: str
    suggested_value: str
    applied: bool = True
    confidence: float = 1.0


@router.post("/apply-suggestion")
async def apply_field_suggestion(
    request: ApplySuggestionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    user_id: int | None = Query(None, description="Admin: scope user id"),
):
    """
    Ghi lại khi user chấp nhận hoặc xem một gợi ý.

    Khi `applied=true` và có đủ `template_id` + `field_key`, ghi thêm Entry +
    UserMemoryItem (embedding RAG nếu bật) để huấn luyện/gợi ý sau này.

    Request body:
    {
        "field_name": "Tên công ty",
        "field_key": "ten_cong_ty",
        "template_id": 12,
        "original_value": "abc xyz",
        "suggested_value": "ABC XYZ Corporation",
        "applied": true,
        "confidence": 0.95
    }
    """
    try:
        effective_user_id = resolve_effective_user_id(current_user, user_id)
        action = "applied" if request.applied else "viewed"
        logger.info(
            "Suggestion %s for %s (key=%s): '%s' → '%s'",
            action,
            request.field_name,
            request.field_key,
            request.original_value,
            request.suggested_value,
        )

        learning_saved = 0
        draft_saved_to_memory = False
        fk = str(request.field_key or "").strip()
        tid = int(request.template_id or 0)
        if request.applied and fk and tid > 0:
            try:
                draft = str(request.original_value or "").strip()
                if draft:
                    UserMemoryService.upsert_memory_value(
                        db=db,
                        user_id=effective_user_id,
                        field_key=fk,
                        value_text=draft[:1000],
                        memory_type="user_draft",
                        source_ref=f"word_ai_rewrite_draft:{tid}",
                        confidence=0.62,
                        is_confirmed=False,
                    )
                    draft_saved_to_memory = True
                learning_saved = persist_word_fields_for_learning(
                    db,
                    user_id=effective_user_id,
                    template_id=tid,
                    data_map={fk: request.suggested_value},
                    source_ref=f"word_ai_rewrite:{tid}",
                    field_keys_filter={fk},
                )
                if learning_saved or draft_saved_to_memory:
                    db.commit()
            except Exception as learning_exc:
                db.rollback()
                logger.warning("Learning persist on AI apply failed: %s", learning_exc)

        return {
            "success": True,
            "message": f"Suggestion {action} successfully",
            "field_name": request.field_name,
            "applied_value": request.suggested_value,
            "confidence": request.confidence,
            "learning_entries_saved": learning_saved,
            "draft_saved_to_memory": draft_saved_to_memory,
        }

    except Exception as e:
        logger.error(f"Error applying suggestion: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to apply suggestion: {str(e)}"
        )
