"""
Admin Service Layer - Xử lý tất cả logic quản lý admin
"""
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import desc, or_, and_, func, text
from app.db.models import User, Form, AuditLog, Entry, WordSubmission, CompositionHistory
import json


class AdminService:
    """Service layer cho các chức năng quản lý của admin"""

    @staticmethod
    def _count_active_admins(db: Session) -> int:
        return db.query(User).filter(
            User.is_admin == True,
            User.is_active == True,
        ).count()

    # ==================== USER MANAGEMENT ====================

    @staticmethod
    def get_system_stats(db: Session) -> Dict[str, Any]:
        """
        Lấy thống kê hệ thống
        """
        try:
            total_users = db.query(User).count()
            active_users = db.query(User).filter(User.is_active == True).count()
            admin_users = db.query(User).filter(User.is_admin == True).count()
            total_forms = db.query(Form).count()
            total_submissions = db.query(Entry).count()
            # Legacy DBs can miss optional CompositionHistory columns (e.g. ai_model).
            # Use raw SQL COUNT to avoid ORM selecting all mapped columns.
            try:
                total_documents = db.execute(
                    text("SELECT COUNT(*) FROM composition_history")
                ).scalar() or 0
            except Exception:
                total_documents = 0
            
            # Tính active users trong 7 ngày gần đây
            seven_days_ago = datetime.utcnow() - timedelta(days=7)
            active_last_7_days = db.query(User).filter(
                User.last_login >= seven_days_ago
            ).count()
            
            # Tính new users trong 30 ngày gần đây
            thirty_days_ago = datetime.utcnow() - timedelta(days=30)
            new_users_30_days = db.query(User).filter(
                User.created_at >= thirty_days_ago
            ).count()

            return {
                "total_users": total_users,
                "active_users": active_users,
                "admin_users": admin_users,
                "inactive_users": total_users - active_users,
                "total_forms": total_forms,
                "total_submissions": total_submissions,
                "total_documents": total_documents,
                "active_last_7_days": active_last_7_days,
                "new_users_30_days": new_users_30_days,
                "timestamp": datetime.utcnow().isoformat()
            }
        except Exception as e:
            raise Exception(f"Error getting system stats: {str(e)}")

    @staticmethod
    def get_users(
        db: Session,
        skip: int = 0,
        limit: int = 10,
        search: Optional[str] = None,
        role: Optional[str] = None,
        status: Optional[str] = None
    ) -> tuple[List[User], int, Dict[str, int]]:
        """
        Lấy danh sách người dùng với filter
        
        :param search: Tìm kiếm theo username, email
        :param role: Filter theo role ('admin', 'user')
        :param status: Filter theo trạng thái ('active', 'inactive')
        :return: (danh sách users, tổng count)
        """
        query = db.query(User)

        if search:
            query = query.filter(
                or_(
                    User.username.ilike(f"%{search}%"),
                    User.email.ilike(f"%{search}%")
                )
            )

        if role == "admin":
            query = query.filter(User.is_admin == True)
        elif role == "user":
            query = query.filter(User.is_admin == False)

        if status == "active":
            query = query.filter(User.is_active == True)
        elif status == "inactive":
            query = query.filter(User.is_active == False)

        total_count = query.count()
        users = query.order_by(desc(User.created_at)).offset(skip).limit(limit).all()

        summary = {
            "total_users": db.query(User).count(),
            "active_users": db.query(User).filter(User.is_active == True).count(),
            "inactive_users": db.query(User).filter(User.is_active == False).count(),
            "admin_users": db.query(User).filter(User.is_admin == True).count(),
        }

        return users, total_count, summary

    @staticmethod
    def get_user_detail(db: Session, user_id: int) -> Optional[User]:
        """Lấy chi tiết của một user"""
        return db.query(User).filter(User.id == user_id).first()

    @staticmethod
    def create_user(
        db: Session,
        username: str,
        email: str,
        password_hash: str,
        is_admin: bool = False,
        admin_id: int = None
    ) -> User:
        """
        Tạo user mới
        """
        try:
            # Kiểm tra email đã tồn tại
            existing_email = db.query(User).filter(func.lower(User.email) == email.lower()).first()
            if existing_email:
                raise ValueError(f"Email {email} đã tồn tại")
            existing_username = db.query(User).filter(func.lower(User.username) == username.lower()).first()
            if existing_username:
                raise ValueError(f"Tên đăng nhập {username} đã tồn tại")

            # Tạo user mới
            new_user = User(
                username=username,
                email=email,
                password_hash=password_hash,
                is_admin=is_admin,
                is_active=True
            )

            db.add(new_user)
            db.flush()  # Lấy ID mới tạo
            db.commit()
            db.refresh(new_user)

            # Ghi log
            AdminService.create_audit_log(
                db=db,
                admin_id=admin_id,
                action="user_created",
                object_type="user",
                object_id=new_user.id,
                object_name=new_user.username,
                description=f"Tạo user mới: {new_user.username}"
            )

            return new_user
        except ValueError:
            db.rollback()
            raise
        except Exception as e:
            db.rollback()
            raise Exception(f"Error creating user: {str(e)}")

    @staticmethod
    def update_user(
        db: Session,
        user_id: int,
        username: Optional[str] = None,
        is_active: Optional[bool] = None,
        is_admin: Optional[bool] = None,
        admin_id: int = None
    ) -> Optional[User]:
        """
        Cập nhật thông tin user
        """
        try:
            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                raise ValueError(f"User ID {user_id} không tồn tại")

            old_value = {
                "username": user.username,
                "is_active": user.is_active,
                "is_admin": user.is_admin
            }

            changes = {}
            if username is not None and username != user.username:
                existing_username = db.query(User).filter(
                    func.lower(User.username) == username.lower(),
                    User.id != user_id
                ).first()
                if existing_username:
                    raise ValueError(f"Tên đăng nhập {username} đã tồn tại")
                user.username = username
                changes["username"] = username
            
            if is_active is not None and is_active != user.is_active:
                if user.is_admin and user.is_active and is_active is False:
                    if AdminService._count_active_admins(db) <= 1:
                        raise ValueError("Không thể vô hiệu hóa admin cuối cùng của hệ thống")
                user.is_active = is_active
                changes["is_active"] = is_active
            
            if is_admin is not None and is_admin != user.is_admin:
                if user.is_admin and user.is_active and is_admin is False:
                    if AdminService._count_active_admins(db) <= 1:
                        raise ValueError("Không thể gỡ quyền admin cuối cùng của hệ thống")
                user.is_admin = is_admin
                changes["is_admin"] = is_admin

            if not changes:
                return user

            user.updated_at = datetime.utcnow()
            db.commit()
            db.refresh(user)

            # Ghi log
            AdminService.create_audit_log(
                db=db,
                admin_id=admin_id,
                action="user_updated",
                object_type="user",
                object_id=user.id,
                object_name=user.username,
                old_value=json.dumps(old_value),
                new_value=json.dumps(changes),
                description=f"Cập nhật user: {user.username}"
            )

            return user
        except ValueError:
            db.rollback()
            raise
        except Exception as e:
            db.rollback()
            raise Exception(f"Error updating user: {str(e)}")

    @staticmethod
    def delete_user(
        db: Session,
        user_id: int,
        admin_id: int = None
    ) -> bool:
        """
        Xóa user (hard delete)
        """
        try:
            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                raise ValueError(f"User ID {user_id} không tồn tại")

            if user.is_admin and user_id != admin_id:
                # Không cho phép xóa admin khác ngoài chính mình
                raise ValueError("Không thể xóa tài khoản admin khác")

            if user.is_admin and user.is_active and AdminService._count_active_admins(db) <= 1:
                raise ValueError("Không thể vô hiệu hóa admin cuối cùng của hệ thống")

            username = user.username
            target_id = user.id
            db.delete(user)
            db.commit()

            # Ghi log
            AdminService.create_audit_log(
                db=db,
                admin_id=admin_id,
                action="user_deleted",
                object_type="user",
                object_id=target_id,
                object_name=username,
                description=f"Xóa user: {username}"
            )

            return True
        except ValueError:
            db.rollback()
            raise
        except Exception as e:
            db.rollback()
            raise Exception(f"Error deleting user: {str(e)}")

    @staticmethod
    def set_user_lock_status(
        db: Session,
        user_id: int,
        is_locked: bool,
        admin_id: int = None
    ) -> Optional[User]:
        """
        Khóa hoặc mở khóa user thông qua is_active.
        """
        try:
            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                raise ValueError(f"User ID {user_id} không tồn tại")

            target_is_active = not is_locked
            if user.is_active == target_is_active:
                return user

            if is_locked and user.is_admin and user.is_active and AdminService._count_active_admins(db) <= 1:
                raise ValueError("Không thể khóa admin cuối cùng của hệ thống")

            old_status = user.is_active
            user.is_active = target_is_active
            user.updated_at = datetime.utcnow()
            db.commit()
            db.refresh(user)

            action = "user_locked" if is_locked else "user_unlocked"
            description = f"{'Khóa' if is_locked else 'Mở khóa'} user: {user.username}"
            AdminService.create_audit_log(
                db=db,
                admin_id=admin_id,
                action=action,
                object_type="user",
                object_id=user.id,
                object_name=user.username,
                old_value=json.dumps({"is_active": old_status}),
                new_value=json.dumps({"is_active": user.is_active}),
                description=description
            )
            return user
        except ValueError:
            db.rollback()
            raise
        except Exception as e:
            db.rollback()
            raise Exception(f"Error setting lock status: {str(e)}")

    @staticmethod
    def toggle_user_admin_role(
        db: Session,
        user_id: int,
        admin_id: int = None
    ) -> bool:
        """
        Thay đổi quyền admin của user
        """
        try:
            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                raise ValueError(f"User ID {user_id} không tồn tại")

            if user.is_admin and user.is_active and AdminService._count_active_admins(db) <= 1:
                raise ValueError("Không thể gỡ quyền admin cuối cùng của hệ thống")

            old_is_admin = user.is_admin
            user.is_admin = not user.is_admin
            user.updated_at = datetime.utcnow()
            db.commit()

            # Ghi log
            action = "admin_role_granted" if user.is_admin else "admin_role_revoked"
            AdminService.create_audit_log(
                db=db,
                admin_id=admin_id,
                action=action,
                object_type="user",
                object_id=user.id,
                object_name=user.username,
                old_value=json.dumps({"is_admin": old_is_admin}),
                new_value=json.dumps({"is_admin": user.is_admin}),
                description=f"{'Thêm' if user.is_admin else 'Gỡ'} quyền admin: {user.username}"
            )

            return True
        except ValueError:
            db.rollback()
            raise
        except Exception as e:
            db.rollback()
            raise Exception(f"Error toggling admin role: {str(e)}")

    # ==================== FORM MANAGEMENT ====================

    @staticmethod
    def get_forms(
        db: Session,
        skip: int = 0,
        limit: int = 10,
        search: Optional[str] = None,
        form_type: Optional[str] = None
    ) -> tuple[List[Form], int]:
        """
        Lấy danh sách forms với filter
        """
        query = db.query(Form)

        if search:
            query = query.filter(Form.form_name.ilike(f"%{search}%"))

        if form_type:
            query = query.filter(Form.form_type == form_type)

        total_count = query.count()
        forms = query.order_by(desc(Form.created_at)).offset(skip).limit(limit).all()

        return forms, total_count

    @staticmethod
    def get_form_detail(db: Session, form_id: int) -> Optional[Form]:
        """Lấy chi tiết form"""
        return db.query(Form).filter(Form.id == form_id).first()

    @staticmethod
    def delete_form(
        db: Session,
        form_id: int,
        admin_id: int = None
    ) -> bool:
        """
        Xóa form
        """
        try:
            form = db.query(Form).filter(Form.id == form_id).first()
            if not form:
                raise ValueError(f"Form ID {form_id} không tồn tại")

            form_name = form.form_name
            db.delete(form)
            db.commit()

            # Ghi log
            AdminService.create_audit_log(
                db=db,
                admin_id=admin_id,
                action="form_deleted",
                object_type="form",
                object_id=form_id,
                object_name=form_name,
                description=f"Xóa form: {form_name}"
            )

            return True
        except Exception as e:
            db.rollback()
            raise Exception(f"Error deleting form: {str(e)}")

    @staticmethod
    def get_forms_statistics(db: Session) -> Dict[str, Any]:
        """
        Lấy thống kê về forms
        """
        try:
            total_forms = db.query(Form).count()
            word_forms = db.query(Form).filter(Form.form_type == "word").count()
            excel_forms = db.query(Form).filter(Form.form_type == "excel").count()
            standard_forms = db.query(Form).filter(Form.form_type == "standard").count()
            template_forms = db.query(Form).filter(Form.is_template == True).count()
            total_submissions = db.query(Entry).count()

            return {
                "total_forms": total_forms,
                "word_forms": word_forms,
                "excel_forms": excel_forms,
                "standard_forms": standard_forms,
                "template_forms": template_forms,
                "total_submissions": total_submissions
            }
        except Exception as e:
            raise Exception(f"Error getting forms statistics: {str(e)}")

    # ==================== AUDIT LOG ====================

    @staticmethod
    def create_audit_log(
        db: Session,
        admin_id: Optional[int],
        action: str,
        object_type: str,
        object_id: Optional[int] = None,
        object_name: Optional[str] = None,
        description: Optional[str] = None,
        old_value: Optional[str] = None,
        new_value: Optional[str] = None,
        ip_address: Optional[str] = None,
        status: str = "success",
        error_message: Optional[str] = None
    ) -> AuditLog:
        """
        Tạo audit log entry
        """
        try:
            log_entry = AuditLog(
                admin_id=admin_id,
                action=action,
                object_type=object_type,
                object_id=object_id,
                object_name=object_name,
                description=description,
                old_value=old_value,
                new_value=new_value,
                ip_address=ip_address,
                status=status,
                error_message=error_message,
                created_at=datetime.utcnow()
            )

            db.add(log_entry)
            db.commit()
            db.refresh(log_entry)

            return log_entry
        except Exception as e:
            db.rollback()
            raise Exception(f"Error creating audit log: {str(e)}")

    @staticmethod
    def get_audit_logs(
        db: Session,
        skip: int = 0,
        limit: int = 50,
        action: Optional[str] = None,
        admin_id: Optional[int] = None,
        object_type: Optional[str] = None,
        days: int = 30
    ) -> tuple[List[AuditLog], int]:
        """
        Lấy danh sách audit logs với filter
        """
        query = db.query(AuditLog)

        # Mặc định chỉ lấy logs trong 30 ngày gần đây
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        query = query.filter(AuditLog.created_at >= cutoff_date)

        if action:
            query = query.filter(AuditLog.action == action)

        if admin_id:
            query = query.filter(AuditLog.admin_id == admin_id)

        if object_type:
            query = query.filter(AuditLog.object_type == object_type)

        total_count = query.count()
        logs = query.order_by(desc(AuditLog.created_at)).offset(skip).limit(limit).all()

        return logs, total_count

    @staticmethod
    def get_user_activity_summary(db: Session, user_id: int, days: int = 7) -> Dict[str, Any]:
        """
        Lấy tóm tắt hoạt động của một user
        """
        try:
            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                raise ValueError(f"User ID {user_id} không tồn tại")

            cutoff_date = datetime.utcnow() - timedelta(days=days)

            # Form entries
            form_entries = db.query(Entry).filter(
                and_(
                    Entry.user_id == user_id,
                    Entry.created_at >= cutoff_date
                )
            ).count()

            # Submissions
            submissions = db.query(WordSubmission).filter(
                and_(
                    WordSubmission.user_id == user_id,
                    WordSubmission.created_at >= cutoff_date
                )
            ).count()

            # Compositions
            try:
                compositions = db.execute(
                    text(
                        "SELECT COUNT(*) FROM composition_history "
                        "WHERE user_id = :user_id AND created_at >= :cutoff_date"
                    ),
                    {
                        "user_id": user_id,
                        "cutoff_date": cutoff_date,
                    },
                ).scalar() or 0
            except Exception:
                compositions = 0

            return {
                "user_id": user_id,
                "username": user.username,
                "email": user.email,
                "last_login": user.last_login.isoformat() if user.last_login else None,
                "form_entries": form_entries,
                "submissions": submissions,
                "compositions": compositions,
                "period_days": days
            }
        except Exception as e:
            raise Exception(f"Error getting user activity: {str(e)}")

    # ==================== ADMIN ACCOUNT ====================

    @staticmethod
    def update_admin_password(
        db: Session,
        admin_id: int,
        new_password_hash: str
    ) -> bool:
        """
        Cập nhật mật khẩu của admin
        """
        try:
            admin = db.query(User).filter(
                and_(
                    User.id == admin_id,
                    User.is_admin == True
                )
            ).first()

            if not admin:
                raise ValueError("Admin không tồn tại")

            admin.password_hash = new_password_hash
            admin.updated_at = datetime.utcnow()
            db.commit()

            # Ghi log
            AdminService.create_audit_log(
                db=db,
                admin_id=admin_id,
                action="admin_password_changed",
                object_type="admin",
                object_id=admin_id,
                object_name=admin.username,
                description=f"Admin {admin.username} đã đổi mật khẩu"
            )

            return True
        except Exception as e:
            db.rollback()
            raise Exception(f"Error updating admin password: {str(e)}")

    @staticmethod
    def get_admin_info(db: Session, admin_id: int) -> Optional[Dict[str, Any]]:
        """
        Lấy thông tin admin
        """
        admin = db.query(User).filter(
            and_(
                User.id == admin_id,
                User.is_admin == True
            )
        ).first()

        if not admin:
            return None

        # Lấy số lượng actions của admin
        action_count = db.query(AuditLog).filter(
            AuditLog.admin_id == admin_id
        ).count()

        # Lấy actions gần nhất
        recent_actions = db.query(AuditLog).filter(
            AuditLog.admin_id == admin_id
        ).order_by(desc(AuditLog.created_at)).limit(5).all()

        return {
            "id": admin.id,
            "username": admin.username,
            "email": admin.email,
            "is_active": admin.is_active,
            "created_at": admin.created_at.isoformat(),
            "last_login": admin.last_login.isoformat() if admin.last_login else None,
            "total_actions": action_count,
            "recent_actions": [
                {
                    "id": action.id,
                    "action": action.action,
                    "object_type": action.object_type,
                    "object_name": action.object_name,
                    "created_at": action.created_at.isoformat()
                }
                for action in recent_actions
            ]
        }
