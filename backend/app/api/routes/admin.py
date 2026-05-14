"""
Admin Routes - API endpoints for admin panel
"""
from fastapi import APIRouter, Depends, HTTPException, status, Request
from typing import Optional, List
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models import User
from app.services.admin_service import AdminService
from app.core.auth import get_current_user, verify_admin
from app.core.security import get_password_hash

# Schemas
from pydantic import BaseModel


class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    is_admin: bool
    is_active: bool
    created_at: str
    last_login: Optional[str]

    class Config:
        from_attributes = True


class UserCreateRequest(BaseModel):
    username: str
    email: str
    password: str
    is_admin: bool = False


class UserUpdateRequest(BaseModel):
    username: Optional[str] = None
    is_active: Optional[bool] = None


class AuditLogResponse(BaseModel):
    id: int
    admin_id: int
    action: str
    object_type: str
    object_id: Optional[int]
    object_name: Optional[str]
    description: Optional[str]
    status: str
    created_at: str

    class Config:
        from_attributes = True


class SystemStatsResponse(BaseModel):
    total_users: int
    active_users: int
    admin_users: int
    inactive_users: int
    total_forms: int
    total_submissions: int
    total_documents: int
    active_last_7_days: int
    new_users_30_days: int
    timestamp: str


class FormResponse(BaseModel):
    id: int
    user_id: int
    form_name: str
    description: Optional[str]
    form_type: str
    is_template: bool
    created_at: str

    class Config:
        from_attributes = True


router = APIRouter(prefix="/api/admin", tags=["admin"])


# ==================== HELPER FUNCTIONS ====================

def get_client_ip(request: Request) -> str:
    """Lấy IP của client"""
    if request.client:
        return request.client.host
    return "unknown"


# ==================== SYSTEM STATS ====================

@router.get("/stats", response_model=SystemStatsResponse)
async def get_system_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Lấy thống kê hệ thống
    Requires: Admin access
    """
    verify_admin(current_user)

    try:
        stats = AdminService.get_system_stats(db)
        return stats
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


# ==================== USER MANAGEMENT ====================

@router.get("/users", response_model=dict)
async def list_users(
    skip: int = 0,
    limit: int = 10,
    search: Optional[str] = None,
    role: Optional[str] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Lấy danh sách người dùng
    Requires: Admin access
    """
    verify_admin(current_user)

    try:
        users, total, summary = AdminService.get_users(
            db=db,
            skip=skip,
            limit=limit,
            search=search,
            role=role,
            status=status
        )

        return {
            "data": [
                {
                    "id": u.id,
                    "username": u.username,
                    "email": u.email,
                    "is_admin": u.is_admin,
                    "is_active": u.is_active,
                    "created_at": u.created_at.isoformat(),
                    "last_login": u.last_login.isoformat() if u.last_login else None
                }
                for u in users
            ],
            "total": total,
            "summary": summary,
            "skip": skip,
            "limit": limit
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.get("/users/{user_id}", response_model=dict)
async def get_user_detail(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Lấy chi tiết người dùng
    Requires: Admin access
    """
    verify_admin(current_user)

    try:
        user = AdminService.get_user_detail(db, user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User không tồn tại"
            )

        # Lấy thông tin hoạt động
        activity = AdminService.get_user_activity_summary(db, user_id)

        return {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "is_admin": user.is_admin,
            "is_active": user.is_active,
            "created_at": user.created_at.isoformat(),
            "last_login": user.last_login.isoformat() if user.last_login else None,
            "activity": activity
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.post("/users", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_user(
    user_data: UserCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Tạo người dùng mới
    Requires: Admin access
    """
    verify_admin(current_user)

    try:
        # Hash password
        password_hash = get_password_hash(user_data.password)

        # Tạo user
        new_user = AdminService.create_user(
            db=db,
            username=user_data.username,
            email=user_data.email,
            password_hash=password_hash,
            is_admin=user_data.is_admin,
            admin_id=current_user.id
        )

        return {
            "id": new_user.id,
            "username": new_user.username,
            "email": new_user.email,
            "is_admin": new_user.is_admin,
            "is_active": new_user.is_active,
            "message": "User đã được tạo thành công"
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.put("/users/{user_id}", response_model=dict)
async def update_user(
    user_id: int,
    user_data: UserUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Cập nhật thông tin người dùng
    Requires: Admin access
    """
    verify_admin(current_user)

    try:
        updated_user = AdminService.update_user(
            db=db,
            user_id=user_id,
            username=user_data.username,
            is_active=user_data.is_active,
            admin_id=current_user.id
        )

        return {
            "id": updated_user.id,
            "username": updated_user.username,
            "email": updated_user.email,
            "is_admin": updated_user.is_admin,
            "is_active": updated_user.is_active,
            "message": "User đã được cập nhật"
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.delete("/users/{user_id}", response_model=dict)
async def delete_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Xóa vĩnh viễn người dùng
    Requires: Admin access
    """
    verify_admin(current_user)

    try:
        # Không cho phép xóa chính bản thân
        if user_id == current_user.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Không thể xóa tài khoản của chính mình"
            )

        AdminService.delete_user(
            db=db,
            user_id=user_id,
            admin_id=current_user.id
        )

        return {"message": "Tài khoản đã được xóa"}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/users/{user_id}/lock", response_model=dict)
async def lock_user_account(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Khóa tài khoản người dùng.
    Requires: Admin access
    """
    verify_admin(current_user)

    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Không thể tự khóa tài khoản của chính mình"
        )

    try:
        user = AdminService.set_user_lock_status(
            db=db,
            user_id=user_id,
            is_locked=True,
            admin_id=current_user.id
        )
        return {
            "id": user.id,
            "is_active": user.is_active,
            "message": f"Đã khóa tài khoản {user.username}"
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/users/{user_id}/unlock", response_model=dict)
async def unlock_user_account(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Mở khóa tài khoản người dùng.
    Requires: Admin access
    """
    verify_admin(current_user)

    try:
        user = AdminService.set_user_lock_status(
            db=db,
            user_id=user_id,
            is_locked=False,
            admin_id=current_user.id
        )
        return {
            "id": user.id,
            "is_active": user.is_active,
            "message": f"Đã mở khóa tài khoản {user.username}"
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/users/{user_id}/toggle-admin", response_model=dict)
async def toggle_admin_role(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Thay đổi quyền admin của user
    Requires: Admin access
    """
    verify_admin(current_user)

    try:
        AdminService.toggle_user_admin_role(
            db=db,
            user_id=user_id,
            admin_id=current_user.id
        )

        user = AdminService.get_user_detail(db, user_id)

        return {
            "id": user.id,
            "is_admin": user.is_admin,
            "message": f"Quyền admin của {user.username} đã được cập nhật"
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


# ==================== FORM MANAGEMENT ====================

@router.get("/forms", response_model=dict)
async def list_forms(
    skip: int = 0,
    limit: int = 10,
    search: Optional[str] = None,
    form_type: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Lấy danh sách biểu mẫu
    Requires: Admin access
    """
    verify_admin(current_user)

    try:
        forms, total = AdminService.get_forms(
            db=db,
            skip=skip,
            limit=limit,
            search=search,
            form_type=form_type
        )

        return {
            "data": [
                {
                    "id": f.id,
                    "user_id": f.user_id,
                    "form_name": f.form_name,
                    "description": f.description,
                    "form_type": f.form_type,
                    "is_template": f.is_template,
                    "created_at": f.created_at.isoformat()
                }
                for f in forms
            ],
            "total": total,
            "skip": skip,
            "limit": limit
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.get("/forms/stats", response_model=dict)
async def get_forms_statistics(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Lấy thống kê về biểu mẫu
    Requires: Admin access
    """
    verify_admin(current_user)

    try:
        stats = AdminService.get_forms_statistics(db)
        return stats
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.delete("/forms/{form_id}", response_model=dict)
async def delete_form(
    form_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Xóa biểu mẫu
    Requires: Admin access
    """
    verify_admin(current_user)

    try:
        AdminService.delete_form(
            db=db,
            form_id=form_id,
            admin_id=current_user.id
        )

        return {"message": "Form đã được xóa"}
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


# ==================== AUDIT LOG ====================

@router.get("/audit-log", response_model=dict)
async def get_audit_logs(
    skip: int = 0,
    limit: int = 50,
    action: Optional[str] = None,
    object_type: Optional[str] = None,
    days: int = 30,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Lấy danh sách audit logs
    Requires: Admin access
    """
    verify_admin(current_user)

    try:
        logs, total = AdminService.get_audit_logs(
            db=db,
            skip=skip,
            limit=limit,
            action=action,
            object_type=object_type,
            days=days
        )

        return {
            "data": [
                {
                    "id": log.id,
                    "admin_id": log.admin_id,
                    "action": log.action,
                    "object_type": log.object_type,
                    "object_id": log.object_id,
                    "object_name": log.object_name,
                    "description": log.description,
                    "status": log.status,
                    "created_at": log.created_at.isoformat()
                }
                for log in logs
            ],
            "total": total,
            "skip": skip,
            "limit": limit
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


# ==================== ADMIN ACCOUNT ====================

@router.get("/account", response_model=dict)
async def get_admin_info(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Lấy thông tin tài khoản admin hiện tại
    Requires: Admin access
    """
    verify_admin(current_user)

    try:
        info = AdminService.get_admin_info(db, current_user.id)
        if not info:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Admin không tồn tại"
            )

        return info
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/account/change-password", response_model=dict)
async def change_password(
    password_data: dict,  # {"old_password": str, "new_password": str}
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Đổi mật khẩu admin
    Requires: Admin access
    """
    verify_admin(current_user)

    try:
        from app.core.security import verify_password

        # Kiểm tra mật khẩu cũ
        if not verify_password(password_data.get("old_password", ""), current_user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Mật khẩu cũ không chính xác"
            )

        # Hash mật khẩu mới
        new_password_hash = get_password_hash(password_data.get("new_password", ""))

        # Cập nhật mật khẩu
        AdminService.update_admin_password(
            db=db,
            admin_id=current_user.id,
            new_password_hash=new_password_hash
        )

        return {"message": "Mật khẩu đã được cập nhật"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )
