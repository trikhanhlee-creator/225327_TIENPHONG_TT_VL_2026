"""
Authentication routes for user login
"""
from fastapi import APIRouter, Response, Request, Depends
from fastapi.responses import JSONResponse, RedirectResponse
import os
import re
import secrets
import smtplib
import ssl
from email.message import EmailMessage
from typing import Optional, Tuple
from datetime import datetime, timedelta
import json
import base64
import hashlib
import hmac
import time
from urllib.parse import quote, urlencode
from urllib.request import urlopen
from sqlalchemy.orm import Session
from sqlalchemy import func, or_, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from app.db.session import get_db
from app.db.models import User, UserActivity, EmailVerification
from app.core.security import get_password_hash, verify_password
from app.core.config import settings
from app.core.logger import logger

router = APIRouter(prefix="/api/auth", tags=["auth"])

sessions = {}  # session_id -> user_info
user_profile_cache = {}  # user_id -> profile extras not mapped in current DB schema

SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", str(60 * 60 * 24 * 2)))  # 2 days inactivity
GUEST_MODE_COOKIE = "guest_mode"
SESSION_COOKIE_NAME = "session_id"
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes"}
NO_STORE_HEADER_VALUE = "no-store, no-cache, must-revalidate, private"
EMAIL_VERIFICATION_TTL_HOURS = int(os.getenv("EMAIL_VERIFICATION_TTL_HOURS", "24"))
GOOGLE_AUTH_BASE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v3/userinfo"
google_oauth_states: dict[str, dict] = {}


def _normalize_email(raw_email: str) -> str:
    return (raw_email or "").strip().lower()


def _is_valid_email(email: str) -> bool:
    """Strict-enough email validator for auth flows."""
    if not email or len(email) > 254 or email.count("@") != 1:
        return False

    local_part, domain_part = email.rsplit("@", 1)
    if not local_part or not domain_part:
        return False
    if local_part.startswith(".") or local_part.endswith(".") or ".." in local_part:
        return False
    if domain_part.startswith(".") or domain_part.endswith(".") or ".." in domain_part:
        return False
    if "." not in domain_part:
        return False

    if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+", local_part):
        return False
    if not re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", domain_part):
        return False

    tld = domain_part.rsplit(".", 1)[-1]
    return len(tld) >= 2 and tld.isalpha()


def _is_mail_delivery_configured() -> bool:
    return bool(settings.SMTP_HOST and settings.SMTP_FROM_EMAIL)


def _is_google_oauth_configured() -> bool:
    return bool(settings.GOOGLE_OAUTH_CLIENT_ID and settings.GOOGLE_OAUTH_CLIENT_SECRET)


def _is_database_available(db: Session) -> bool:
    try:
        db.execute(text("SELECT 1"))
        return True
    except (OperationalError, SQLAlchemyError) as exc:
        logger.error(f"Database unavailable: {exc}")
        return False


def _cleanup_expired_google_states() -> None:
    now = int(time.time())
    expired_keys = [
        state
        for state, payload in google_oauth_states.items()
        if int(payload.get("expires_at", 0)) <= now
    ]
    for key in expired_keys:
        google_oauth_states.pop(key, None)


def _resolve_google_redirect_uri(request: Optional[Request] = None) -> str:
    """Match redirect_uri to the host the user opened (localhost vs 127.0.0.1)."""
    callback_path = "/api/auth/google/callback"
    allowed_hosts = {"localhost:8000", "127.0.0.1:8000"}

    if request is not None:
        host = (request.headers.get("host") or "").strip().lower()
        if host in allowed_hosts:
            scheme = request.url.scheme if request.url.scheme in {"http", "https"} else "http"
            return f"{scheme}://{host}{callback_path}"

    configured = (settings.GOOGLE_OAUTH_REDIRECT_URI or "").strip()
    if configured:
        return configured
    return f"http://127.0.0.1:8000{callback_path}"


def _build_google_auth_url(state: str, redirect_uri: str) -> str:
    query = urlencode(
        {
            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "access_type": "online",
            "prompt": "select_account",
            "state": state,
        }
    )
    return f"{GOOGLE_AUTH_BASE_URL}?{query}"


def _build_verification_link(token: str) -> str:
    base_url = (settings.APP_BASE_URL or "http://localhost:8000").rstrip("/")
    return f"{base_url}/api/auth/verify-email?token={quote(token)}"


def _get_latest_email_verification(db: Session, user_id: int, email: str) -> Optional[EmailVerification]:
    normalized_email = _normalize_email(email)
    return (
        db.query(EmailVerification)
        .filter(
            EmailVerification.user_id == user_id,
            func.lower(EmailVerification.email) == normalized_email,
        )
        .order_by(EmailVerification.created_at.desc())
        .first()
    )


def _is_user_email_verified(db: Session, user: User) -> bool:
    # Admin accounts are treated as privileged and bypass email verification.
    if bool(user.is_admin):
        return True

    latest = _get_latest_email_verification(db, user.id, user.email)
    # Legacy accounts (created before verification flow) are treated as verified.
    if not latest:
        return True
    return bool(latest.is_verified)


def _create_email_verification_request(db: Session, user: User) -> EmailVerification:
    db.query(EmailVerification).filter(
        EmailVerification.user_id == user.id,
        func.lower(EmailVerification.email) == _normalize_email(user.email),
        EmailVerification.is_verified == False,  # noqa: E712
    ).delete(synchronize_session=False)

    verification = EmailVerification(
        user_id=user.id,
        email=_normalize_email(user.email),
        token=secrets.token_urlsafe(48),
        expires_at=datetime.utcnow() + timedelta(hours=EMAIL_VERIFICATION_TTL_HOURS),
        is_verified=False,
    )
    db.add(verification)
    db.commit()
    db.refresh(verification)
    return verification


def _send_verification_email(recipient_email: str, username: str, verification_link: str) -> None:
    if not _is_mail_delivery_configured():
        raise RuntimeError("Email delivery is not configured")

    msg = EmailMessage()
    msg["Subject"] = "Xac thuc email tai khoan AutoFill AI"
    sender_name = (settings.SMTP_FROM_NAME or "AutoFill AI").strip() or "AutoFill AI"
    msg["From"] = f"{sender_name} <{settings.SMTP_FROM_EMAIL}>"
    msg["To"] = recipient_email
    text_body = (
        f"Chao {username},\n\n"
        "Cam on ban da dang ky AutoFill AI.\n"
        "Vui long xac thuc email bang cach mo lien ket duoi day:\n"
        f"{verification_link}\n\n"
        f"Lien ket co hieu luc trong {EMAIL_VERIFICATION_TTL_HOURS} gio.\n"
        "Neu ban khong thuc hien yeu cau nay, vui long bo qua email.\n"
    )
    msg.set_content(text_body)

    smtp_host = settings.SMTP_HOST
    smtp_port = int(settings.SMTP_PORT)
    context = ssl.create_default_context()
    if settings.SMTP_USE_SSL:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15, context=context) as server:
            if settings.SMTP_USERNAME:
                server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD or "")
            server.send_message(msg)
        return

    with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
        if settings.SMTP_USE_TLS:
            server.starttls(context=context)
        if settings.SMTP_USERNAME:
            server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD or "")
        server.send_message(msg)


def _send_or_raise_verification_email(db: Session, user: User) -> EmailVerification:
    verification = _create_email_verification_request(db, user)
    try:
        link = _build_verification_link(verification.token)
        _send_verification_email(user.email, user.username, link)
        return verification
    except Exception as exc:
        logger.error(f"Failed to send verification email to {user.email}: {exc}")
        raise


def _should_auto_verify_without_smtp() -> bool:
    return bool(settings.EMAIL_AUTO_VERIFY_WITHOUT_SMTP) and not _is_mail_delivery_configured()


def _auto_verify_user_email(db: Session, user: User) -> EmailVerification:
    verification = _create_email_verification_request(db, user)
    verification.is_verified = True
    db.commit()
    db.refresh(verification)
    logger.warning(
        "SMTP not configured: auto-verified email for %s (development / EMAIL_AUTO_VERIFY_WITHOUT_SMTP)",
        user.email,
    )
    return verification


def _ensure_user_email_verified_on_login(db: Session, user: User) -> bool:
    """If SMTP is off and dev bypass is on, verify pending accounts so login works."""
    if _is_user_email_verified(db, user):
        return True
    if not _should_auto_verify_without_smtp():
        return False
    latest = _get_latest_email_verification(db, user.id, user.email)
    if latest and not latest.is_verified:
        latest.is_verified = True
        db.commit()
        return True
    if not latest:
        _auto_verify_user_email(db, user)
        return True
    return False


def _provision_email_verification(db: Session, user: User) -> tuple[bool, str]:
    """
    Send verification email when SMTP is configured; otherwise auto-verify in dev.
    Returns (email_sent, user-facing message).
    """
    if _is_mail_delivery_configured():
        _send_or_raise_verification_email(db, user)
        return (
            True,
            "Đăng ký thành công! Vui lòng kiểm tra email để xác thực tài khoản trước khi đăng nhập.",
        )
    if _should_auto_verify_without_smtp():
        _auto_verify_user_email(db, user)
        return (
            False,
            "Đăng ký thành công! Bạn có thể đăng nhập ngay (chế độ phát triển — chưa cấu hình gửi email).",
        )
    raise RuntimeError("Email delivery is not configured")


def _get_session_secret() -> str:
    return os.getenv("SESSION_SECRET", "autofill-dev-session-secret")


def _set_no_store_headers(response: Response) -> None:
    response.headers["Cache-Control"] = NO_STORE_HEADER_VALUE
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"


def _no_store_json_response(status_code: int, content: dict) -> JSONResponse:
    response = JSONResponse(status_code=status_code, content=content)
    _set_no_store_headers(response)
    return response


def _utc_naive_to_unix_millis(value: Optional[datetime]) -> int:
    if not value:
        return 0
    epoch = datetime(1970, 1, 1)
    return int((value - epoch).total_seconds() * 1000)


def generate_session_token(user_id: int) -> str:
    issued_at_ms = int(time.time() * 1000)
    payload = f"{user_id}:{issued_at_ms}"
    signature = hmac.new(
        _get_session_secret().encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    raw = f"{payload}:{signature}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8")


def decode_session_token(token: Optional[str]) -> Optional[Tuple[int, int]]:
    if not token:
        return None

    try:
        decoded = base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8")
        user_id_str, issued_at_str, signature = decoded.split(":", 2)

        payload = f"{user_id_str}:{issued_at_str}"
        expected_signature = hmac.new(
            _get_session_secret().encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(signature, expected_signature):
            return None

        issued_at_raw = int(issued_at_str)
        issued_at_ms = issued_at_raw * 1000 if issued_at_raw < 100_000_000_000 else issued_at_raw
        if (int(time.time() * 1000) - issued_at_ms) > (SESSION_TTL_SECONDS * 1000):
            return None

        user_id = int(user_id_str)
        if user_id <= 0:
            return None
        return user_id, issued_at_ms
    except Exception:
        return None


def verify_session_token(token: Optional[str]) -> Optional[int]:
    payload = decode_session_token(token)
    if not payload:
        return None
    return payload[0]


def _set_session_cookie(response: Response, user_id: int) -> str:
    """Issue a signed session token and attach it as a browser-session cookie."""
    session_id = generate_session_token(user_id)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        httponly=True,
        samesite="Lax",
        secure=SESSION_COOKIE_SECURE,
        path="/",
    )
    _set_no_store_headers(response)
    return session_id


def _set_guest_mode_cookie(response: Response) -> None:
    """Mark browser as guest mode using a session cookie (clears on browser close)."""
    response.set_cookie(
        key=GUEST_MODE_COOKIE,
        value="1",
        httponly=False,
        samesite="Lax",
        secure=SESSION_COOKIE_SECURE,
        path="/",
    )
    _set_no_store_headers(response)


def _clear_guest_mode_cookie(response: Response) -> None:
    response.delete_cookie(GUEST_MODE_COOKIE, path="/")


def get_authenticated_user_from_request(request: Request, db: Session) -> Optional[User]:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    session_payload = decode_session_token(token)
    if not session_payload:
        return None

    user_id, issued_at_ms = session_payload
    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        return None

    latest_logout = (
        db.query(UserActivity.created_at)
        .filter(
            UserActivity.user_id == user_id,
            UserActivity.activity_type == "logout",
        )
        .order_by(UserActivity.created_at.desc())
        .first()
    )
    if latest_logout:
        logout_at_ms = _utc_naive_to_unix_millis(latest_logout[0])
        if issued_at_ms <= logout_at_ms:
            return None

    return user


def ensure_default_admin_privilege(db: Session) -> Optional[User]:
    """Recover default admin role when the system has no active admin."""
    active_admin = db.query(User).filter(
        User.is_admin == True,
        User.is_active == True,
    ).first()
    if active_admin:
        return active_admin

    fallback_admin = db.query(User).filter(
        or_(
            func.lower(User.username) == "admin",
            func.lower(User.email) == "admin@example.com",
        )
    ).order_by(User.id.asc()).first()

    if not fallback_admin:
        return None

    fallback_admin.is_admin = True
    fallback_admin.is_active = True
    fallback_admin.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(fallback_admin)
    return fallback_admin

def generate_session_id():
    """Generate a simple session ID"""
    import uuid
    return str(uuid.uuid4())


@router.post("/guest/start")
async def start_guest_mode():
    """Start guest mode session (browser-session scoped)."""
    response = JSONResponse(
        status_code=200,
        content={
            "success": True,
            "message": "Đã bật chế độ dùng miễn phí. Lịch sử sẽ mất khi tắt trình duyệt.",
            "mode": "guest",
        }
    )
    _set_guest_mode_cookie(response)
    return response


@router.get("/google/start")
async def google_oauth_start(request: Request, next: str = "/home", db: Session = Depends(get_db)):
    """Start Google OAuth login flow."""
    if not _is_google_oauth_configured():
        return RedirectResponse(url="/login?google=not_configured", status_code=303)

    if not _is_database_available(db):
        return RedirectResponse(url="/login?google=db_unavailable", status_code=303)

    safe_next = (next or "/home").strip()
    if not safe_next.startswith("/") or safe_next.startswith("//"):
        safe_next = "/home"

    redirect_uri = _resolve_google_redirect_uri(request)
    _cleanup_expired_google_states()
    state = secrets.token_urlsafe(24)
    google_oauth_states[state] = {
        "next": safe_next,
        "redirect_uri": redirect_uri,
        "created_at": int(time.time()),
        "expires_at": int(time.time()) + int(settings.GOOGLE_OAUTH_STATE_TTL_SECONDS),
    }

    return RedirectResponse(url=_build_google_auth_url(state, redirect_uri), status_code=303)


@router.get("/google/config")
async def google_oauth_config_status():
    """Expose Google OAuth readiness for login UI."""
    ready = _is_google_oauth_configured()
    return JSONResponse(
        status_code=200,
        content={
            "enabled": ready,
            "configured": ready,
            "missing": [] if ready else [
                name for name, value in {
                    "GOOGLE_OAUTH_CLIENT_ID": settings.GOOGLE_OAUTH_CLIENT_ID,
                    "GOOGLE_OAUTH_CLIENT_SECRET": settings.GOOGLE_OAUTH_CLIENT_SECRET,
                }.items() if not value
            ],
        },
    )


@router.get("/google/callback")
async def google_oauth_callback(code: str = "", state: str = "", db: Session = Depends(get_db)):
    """Google OAuth callback: only allow existing registered emails."""
    if not _is_google_oauth_configured():
        return RedirectResponse(url="/login?google=not_configured", status_code=303)

    _cleanup_expired_google_states()
    state_payload = google_oauth_states.pop((state or "").strip(), None)
    if not state_payload:
        return RedirectResponse(url="/login?google=invalid_state", status_code=303)

    next_url = state_payload.get("next", "/home")
    redirect_uri = (state_payload.get("redirect_uri") or settings.GOOGLE_OAUTH_REDIRECT_URI or "").strip()
    if not redirect_uri:
        redirect_uri = _resolve_google_redirect_uri()
    if not code:
        return RedirectResponse(url="/login?google=missing_code", status_code=303)

    try:
        token_payload = urlencode(
            {
                "code": code,
                "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
                "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            }
        ).encode("utf-8")

        token_request = urlopen(
            GOOGLE_TOKEN_ENDPOINT,
            data=token_payload,
            timeout=15,
        )
        token_response = json.loads(token_request.read().decode("utf-8"))
        if token_response.get("error"):
            logger.error(
                "Google token exchange failed: %s - %s",
                token_response.get("error"),
                token_response.get("error_description"),
            )
            return RedirectResponse(url="/login?google=token_error", status_code=303)
        access_token = (token_response.get("access_token") or "").strip()
        if not access_token:
            return RedirectResponse(url="/login?google=token_error", status_code=303)

        userinfo_request = urlopen(
            f"{GOOGLE_USERINFO_ENDPOINT}?{urlencode({'access_token': access_token})}",
            timeout=15,
        )
        profile = json.loads(userinfo_request.read().decode("utf-8"))
        email = _normalize_email(profile.get("email", ""))
        email_verified = bool(profile.get("email_verified"))
        if not email or not _is_valid_email(email) or not email_verified:
            return RedirectResponse(url="/login?google=invalid_email", status_code=303)

        user = db.query(User).filter(func.lower(User.email) == email).first()
        if not user:
            return RedirectResponse(url="/login?google=email_not_registered", status_code=303)
        if not user.is_active:
            return RedirectResponse(url="/login?google=account_disabled", status_code=303)
        if not _is_user_email_verified(db, user) and not _ensure_user_email_verified_on_login(db, user):
            return RedirectResponse(url="/login?google=email_not_verified", status_code=303)

        user.last_login = datetime.utcnow()
        db.add(UserActivity(
            user_id=user.id,
            activity_type="login_google",
            feature="auth",
            path="/google/callback",
            method="GET",
            description="User login successful via Google OAuth",
        ))
        db.commit()
        db.refresh(user)

        user_info = {
            "user_id": user.id,
            "username": user.username,
            "email": user.email,
            "is_admin": bool(user.is_admin),
            "login_time": datetime.utcnow().isoformat(),
        }
        response = RedirectResponse(url=next_url, status_code=303)
        session_id = _set_session_cookie(response, user.id)
        _clear_guest_mode_cookie(response)
        sessions[session_id] = user_info
        return response
    except OperationalError as exc:
        logger.error(f"Google OAuth callback failed (database): {exc}")
        return RedirectResponse(url="/login?google=db_unavailable", status_code=303)
    except SQLAlchemyError as exc:
        logger.error(f"Google OAuth callback failed (database): {exc}")
        return RedirectResponse(url="/login?google=db_unavailable", status_code=303)
    except Exception as exc:
        logger.error(f"Google OAuth callback failed: {exc}")
        return RedirectResponse(url="/login?google=oauth_error", status_code=303)

@router.post("/login")
async def login(request: Request, db: Session = Depends(get_db)):
    """Handle user login"""
    try:
        data = await request.json()
        identifier = (data.get("identifier") or data.get("username") or "").strip()
        password = data.get("password", "").strip()
        
        if not identifier or not password:
            return _no_store_json_response(
                status_code=400,
                content={"error": "Tên đăng nhập/email và mật khẩu không được để trống"}
            )

        if "@" in identifier:
            email = _normalize_email(identifier)
            if not _is_valid_email(email):
                return _no_store_json_response(
                    status_code=400,
                    content={"error": "Email đăng nhập không đúng định dạng"}
                )
            user = db.query(User).filter(func.lower(User.email) == email).first()
        else:
            user = db.query(User).filter(func.lower(User.username) == identifier.lower()).first()

        if not user:
            return _no_store_json_response(
                status_code=401,
                content={"error": "Thông tin đăng nhập không chính xác"}
            )

        # Support legacy plaintext values while migrating existing data
        password_ok = verify_password(password, user.password_hash) or (user.password_hash == password)
        if not password_ok:
            return _no_store_json_response(
                status_code=401,
                content={"error": "Thông tin đăng nhập không chính xác"}
            )

        if not user.is_active:
            return _no_store_json_response(
                status_code=403,
                content={"error": "Tài khoản đã bị khóa. Vui lòng liên hệ quản trị viên."}
            )

        if not _is_user_email_verified(db, user):
            if _ensure_user_email_verified_on_login(db, user):
                pass
            elif not _is_user_email_verified(db, user):
                email_sent = False
                email_error = None
                try:
                    _send_or_raise_verification_email(db, user)
                    email_sent = True
                except Exception as exc:
                    email_error = str(exc)

            if not _is_user_email_verified(db, user) and email_sent:
                return _no_store_json_response(
                    status_code=403,
                    content={
                        "error": "Email chưa được xác thực. Chúng tôi đã gửi lại email xác thực, vui lòng kiểm tra hộp thư.",
                        "needs_email_verification": True,
                    },
                )

            if not _is_user_email_verified(db, user):
                logger.warning(f"Could not resend verification email for {user.email}: {email_error}")
                return _no_store_json_response(
                    status_code=403,
                    content={
                        "error": "Email chưa được xác thực. Hệ thống chưa thể gửi email xác thực, vui lòng liên hệ quản trị viên.",
                        "needs_email_verification": True,
                    },
                )

        user.last_login = datetime.utcnow()
        db.add(UserActivity(
            user_id=user.id,
            activity_type="login",
            feature="auth",
            path="/login",
            method="POST",
            description="User login successful",
        ))
        db.commit()
        db.refresh(user)
        
        # Create session
        user_info = {
            "user_id": user.id,
            "username": user.username,
            "email": user.email,
            "is_admin": bool(user.is_admin),
            "login_time": datetime.utcnow().isoformat()
        }
        
        # Return session ID as cookie
        response = JSONResponse(
            status_code=200,
            content={
                "success": True,
                "message": f"Đăng nhập thành công. Chào {user_info['username']}!",
                "user": {
                    "username": user_info["username"],
                    "email": user_info["email"],
                    "is_admin": user_info["is_admin"]
                }
            }
        )
        session_id = _set_session_cookie(response, user.id)
        _clear_guest_mode_cookie(response)
        sessions[session_id] = user_info
        return response
        
    except Exception as e:
        return _no_store_json_response(
            status_code=500,
            content={"error": f"Lỗi đăng nhập: {str(e)}"}
        )

@router.post("/signup")
async def signup(request: Request, db: Session = Depends(get_db)):
    """Handle user registration"""
    try:
        data = await request.json()
        full_name = data.get("full_name", "").strip()
        username = data.get("username", "").strip()
        email = _normalize_email(data.get("email", ""))
        password = data.get("password", "").strip()
        
        if not full_name:
            full_name = username

        # Validation
        if not username or not email or not password:
            return JSONResponse(
                status_code=400,
                content={"error": "Vui lòng điền đầy đủ tên đăng nhập, email và mật khẩu"}
            )

        # Strict email format validation
        if not _is_valid_email(email):
            return JSONResponse(
                status_code=400,
                content={"error": "Email không hợp lệ. Vui lòng nhập đúng địa chỉ email (ví dụ: ten@domain.com)"}
            )
        
        # Check username length
        if len(username) < 3 or len(username) > 20:
            return JSONResponse(
                status_code=400,
                content={"error": "Tên đăng nhập phải từ 3-20 ký tự"}
            )
        
        # Check password length
        if len(password) < 6:
            return JSONResponse(
                status_code=400,
                content={"error": "Mật khẩu phải có tối thiểu 6 ký tự"}
            )

        if not _is_mail_delivery_configured() and not _should_auto_verify_without_smtp():
            return JSONResponse(
                status_code=503,
                content={
                    "error": "Hệ thống chưa cấu hình gửi email xác thực. Vui lòng liên hệ quản trị viên.",
                },
            )
        
        # Check if username already exists
        existing_username = db.query(User).filter(func.lower(User.username) == username.lower()).first()
        if existing_username:
            return JSONResponse(
                status_code=409,
                content={"error": "Tên đăng nhập đã tồn tại"}
            )
        
        # Check if email already exists
        existing_email = db.query(User).filter(func.lower(User.email) == email.lower()).first()
        if existing_email:
            return JSONResponse(
                status_code=409,
                content={"error": "Email đã được sử dụng"}
            )

        # Create new user
        new_user = User(
            username=username,
            email=email,
            password_hash=get_password_hash(password),
            is_admin=False,
            is_active=True
        )
        db.add(new_user)
        db.commit()
        db.refresh(new_user)

        try:
            _, signup_message = _provision_email_verification(db, new_user)
        except Exception:
            db.delete(new_user)
            db.commit()
            return JSONResponse(
                status_code=500,
                content={"error": "Không thể gửi email xác thực. Vui lòng thử lại sau."},
            )

        # Keep extended profile info aligned with /session response shape
        user_profile_cache[new_user.id] = {
            "full_name": full_name,
            "phone": "",
            "address": "",
            "language": "vi",
        }
        
        return JSONResponse(
            status_code=201,
            content={
                "success": True,
                "message": signup_message,
                "user": {
                    "full_name": full_name,
                    "username": username,
                    "email": email
                }
            }
        )
        
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Lỗi đăng ký: {str(e)}"}
        )


@router.get("/verify-email")
async def verify_email(token: str = "", db: Session = Depends(get_db)):
    """Verify account email from verification link."""
    safe_token = (token or "").strip()
    if not safe_token:
        return RedirectResponse(url="/login?verified=invalid", status_code=303)

    verification = db.query(EmailVerification).filter(EmailVerification.token == safe_token).first()
    if not verification:
        return RedirectResponse(url="/login?verified=invalid", status_code=303)

    if verification.is_verified:
        return RedirectResponse(url="/login?verified=already", status_code=303)

    if verification.expires_at and verification.expires_at < datetime.utcnow():
        return RedirectResponse(url="/login?verified=expired", status_code=303)

    user = db.query(User).filter(User.id == verification.user_id).first()
    if not user:
        return RedirectResponse(url="/login?verified=invalid", status_code=303)

    verification.is_verified = True
    user.updated_at = datetime.utcnow()
    db.add(UserActivity(
        user_id=user.id,
        activity_type="email_verified",
        feature="auth",
        path="/verify-email",
        method="GET",
        description="User email verified successfully",
    ))
    db.commit()
    return RedirectResponse(url="/login?verified=success", status_code=303)


@router.post("/resend-verification")
async def resend_verification(request: Request, db: Session = Depends(get_db)):
    """Resend email verification link to an unverified account."""
    try:
        if not _is_mail_delivery_configured() and not _should_auto_verify_without_smtp():
            return JSONResponse(
                status_code=503,
                content={"error": "Hệ thống chưa cấu hình gửi email xác thực."},
            )

        data = await request.json()
        identifier = (data.get("identifier") or data.get("email") or "").strip()
        if not identifier:
            return JSONResponse(
                status_code=400,
                content={"error": "Vui lòng nhập email đã đăng ký."},
            )

        normalized_email = _normalize_email(identifier)
        if not _is_valid_email(normalized_email):
            return JSONResponse(
                status_code=400,
                content={"error": "Email không hợp lệ."},
            )

        user = db.query(User).filter(func.lower(User.email) == normalized_email).first()
        if not user:
            return JSONResponse(
                status_code=200,
                content={
                    "success": True,
                    "message": "Nếu email đã đăng ký, chúng tôi đã gửi lại email xác thực.",
                },
            )

        if _is_user_email_verified(db, user):
            return JSONResponse(
                status_code=200,
                content={
                    "success": True,
                    "message": "Email đã được xác thực, bạn có thể đăng nhập ngay.",
                },
            )

        if _should_auto_verify_without_smtp():
            _auto_verify_user_email(db, user)
            return JSONResponse(
                status_code=200,
                content={
                    "success": True,
                    "message": "Email đã được xác thực, bạn có thể đăng nhập ngay.",
                },
            )

        _send_or_raise_verification_email(db, user)
        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "message": "Đã gửi lại email xác thực. Vui lòng kiểm tra hộp thư.",
            },
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Gửi lại email xác thực thất bại: {str(e)}"},
        )

@router.post("/logout")
async def logout(request: Request, db: Session = Depends(get_db)):
    """Handle user logout"""
    try:
        user = get_authenticated_user_from_request(request, db)
        session_id = request.cookies.get(SESSION_COOKIE_NAME)
        if session_id and session_id in sessions:
            del sessions[session_id]

        if user:
            db.add(UserActivity(
                user_id=user.id,
                activity_type="logout",
                feature="auth",
                path="/logout",
                method="POST",
                description="User logged out",
            ))
            db.commit()
        
        response = JSONResponse(
            status_code=200,
            content={"success": True, "message": "Đã đăng xuất"}
        )
        _set_no_store_headers(response)
        response.delete_cookie(SESSION_COOKIE_NAME, path="/")
        _clear_guest_mode_cookie(response)
        return response
    except Exception as e:
        return _no_store_json_response(
            status_code=500,
            content={"error": f"Lỗi đăng xuất: {str(e)}"}
        )

@router.get("/session")
async def get_session(request: Request, db: Session = Depends(get_db)):
    """Get current session info"""
    try:
        user = get_authenticated_user_from_request(request, db)
        if user:
            response = JSONResponse(
                status_code=200,
                content={
                    "authenticated": True,
                    "guest": False,
                    "mode": "authenticated",
                    "user": {
                        "user_id": user.id,
                        "username": user.username,
                        "email": user.email,
                        "is_admin": bool(user.is_admin),
                        "full_name": (user_profile_cache.get(user.id) or {}).get("full_name", ""),
                        "phone": (user_profile_cache.get(user.id) or {}).get("phone", ""),
                        "address": (user_profile_cache.get(user.id) or {}).get("address", ""),
                        "language": (user_profile_cache.get(user.id) or {}).get("language", "vi"),
                    }
                }
            )
            _set_session_cookie(response, user.id)
            return response
        else:
            guest_mode = request.cookies.get(GUEST_MODE_COOKIE) == "1"
            return _no_store_json_response(
                status_code=200,
                content={
                    "authenticated": False,
                    "guest": guest_mode,
                    "mode": "guest" if guest_mode else "anonymous",
                }
            )
    except Exception as e:
        return _no_store_json_response(
            status_code=500,
            content={"error": str(e)}
        )

@router.get("/check-auth")
async def check_auth(request: Request, db: Session = Depends(get_db)):
    """Check if user is authenticated"""
    user = get_authenticated_user_from_request(request, db)
    if user:
        response = JSONResponse(
            status_code=200,
            content={
                "authenticated": True,
                "guest": False,
                "mode": "authenticated",
                "user": {
                    "user_id": user.id,
                    "username": user.username,
                    "email": user.email,
                    "is_admin": bool(user.is_admin),
                }
            }
        )
        _set_session_cookie(response, user.id)
        return response
    guest_mode = request.cookies.get(GUEST_MODE_COOKIE) == "1"
    return _no_store_json_response(
        status_code=200,
        content={
            "authenticated": False,
            "guest": guest_mode,
            "mode": "guest" if guest_mode else "anonymous",
        }
    )


@router.get("/activity-history")
async def get_activity_history(request: Request, limit: int = 50, db: Session = Depends(get_db)):
    """Get website usage history for current authenticated user only."""
    user = get_authenticated_user_from_request(request, db)
    if not user:
        return JSONResponse(
            status_code=401,
            content={"error": "Bạn chưa đăng nhập"}
        )

    safe_limit = max(1, min(limit, 200))
    rows = (
        db.query(UserActivity)
        .filter(UserActivity.user_id == user.id)
        .order_by(UserActivity.created_at.desc())
        .limit(safe_limit)
        .all()
    )

    activities = []
    for row in rows:
        activities.append({
            "id": row.id,
            "activity_type": row.activity_type,
            "feature": row.feature,
            "path": row.path,
            "method": row.method,
            "description": row.description,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        })

    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "total": len(activities),
            "activities": activities,
        }
    )

@router.put("/update-profile")
async def update_profile(request: Request, db: Session = Depends(get_db)):
    """Update user profile information"""
    try:
        user = get_authenticated_user_from_request(request, db)
        if not user:
            return JSONResponse(
                status_code=401,
                content={"error": "Bạn chưa đăng nhập"}
            )
        
        data = await request.json()

        # Persist fields available in current DB schema
        user.email = data.get("email", user.email)
        user.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(user)

        # Keep extended profile settings in memory for current runtime
        user_profile_cache[user.id] = {
            "full_name": data.get("full_name", ""),
            "phone": data.get("phone", ""),
            "address": data.get("address", ""),
            "language": data.get("language", "vi"),
        }
        
        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "message": "Cập nhật hồ sơ thành công",
                "user": {
                    "user_id": user.id,
                    "username": user.username,
                    "email": user.email,
                    "is_admin": bool(user.is_admin),
                    "full_name": user_profile_cache[user.id]["full_name"],
                    "phone": user_profile_cache[user.id]["phone"],
                    "address": user_profile_cache[user.id]["address"],
                    "language": user_profile_cache[user.id]["language"],
                }
            }
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Lỗi cập nhật hồ sơ: {str(e)}"}
        )

@router.post("/change-password")
async def change_password(request: Request, db: Session = Depends(get_db)):
    """Change user password"""
    try:
        user = get_authenticated_user_from_request(request, db)
        if not user:
            return JSONResponse(
                status_code=401,
                content={"error": "Bạn chưa đăng nhập"}
            )
        
        data = await request.json()
        current_password = data.get("current_password", "").strip()
        new_password = data.get("new_password", "").strip()
        
        if not current_password or not new_password:
            return JSONResponse(
                status_code=400,
                content={"error": "Vui lòng điền đầy đủ thông tin"}
            )
        
        if len(new_password) < 6:
            return JSONResponse(
                status_code=400,
                content={"error": "Mật khẩu mới phải có ít nhất 6 ký tự"}
            )
        
        # Verify current password
        password_ok = verify_password(current_password, user.password_hash) or (user.password_hash == current_password)
        if not password_ok:
            return JSONResponse(
                status_code=401,
                content={"error": "Mật khẩu hiện tại không chính xác"}
            )

        # Update password
        user.password_hash = get_password_hash(new_password)
        db.commit()
        
        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "message": "Thay đổi mật khẩu thành công"
            }
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Lỗi thay đổi mật khẩu: {str(e)}"}
        )
