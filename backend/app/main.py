from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, HTMLResponse, Response
from sqlalchemy.orm import Session
import os
import re

from app.core.config import settings
from app.core.logger import logger
from app.api.routes import suggestions, word, form_replacement, excel, composer, auth, form_edit, admin, payment
from app.db.session import engine, Base, SessionLocal, get_db
from app.db.models import User, UserActivity

# Tạo các table nếu chưa tồn tại
Base.metadata.create_all(bind=engine)

# Khởi tạo FastAPI app
app = FastAPI(
    title=settings.PROJECT_NAME,
    version="1.0.0",
    description="API tự động điền mẫu với gợi ý từ lịch sử sử dụng AI"
)


@app.on_event("startup")
async def ensure_legacy_schema_compatibility():
    """Patch legacy DB schema before serving requests."""
    db = SessionLocal()
    try:
        word.ensure_forms_schema_compatibility(db)
        composer.ensure_composer_schema_compatibility(db)
        recovered_admin = auth.ensure_default_admin_privilege(db)
        if recovered_admin:
            logger.info(f"Default admin check completed with user: {recovered_admin.username}")
        else:
            logger.warning("No active admin found and no fallback admin account could be recovered")
    except Exception as e:
        logger.error(f"Legacy schema compatibility check failed: {e}")
    finally:
        db.close()

# Cấu hình CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_PAGE_REDIRECTS = {
    "/static/login.html": "/login",
    "/static/register.html": "/register",
    "/static/menu.html": "/home",
    "/static/form.html": "/form",
    "/static/excel-upload.html": "/excel",
    "/static/word-upload.html": "/word-upload",
    "/static/composer.html": "/composer",
    "/static/payment.html": "/payment",
    "/static/user-account.html": "/user-account",
    "/static/admin-dashboard.html": "/admin-dashboard",
    "/static/admin-users.html": "/admin-users",
    "/static/admin-forms.html": "/admin-forms",
    "/static/admin-reports.html": "/admin-reports",
    "/static/admin-audit-log.html": "/admin-audit-log",
    "/static/admin-account.html": "/admin-account",
}

GLOBAL_SYSTEM_FOOTER_STYLE = """
<style id="global-system-footer-style">
.global-system-footer{
    background:#ececec;
    border-top:1px solid #dfdfdf;
    margin-top:24px;
}
.global-system-footer__inner{
    max-width:1280px;
    margin:0 auto;
    padding:14px 32px 12px;
    display:flex;
    justify-content:space-between;
    align-items:center;
    gap:14px;
    flex-wrap:wrap;
}
.global-system-footer__brand{
    color:#0f172a;
    font-size:14px;
    font-weight:700;
    margin:0;
}
.global-system-footer__meta{
    color:#1f2937;
    font-size:12px;
    margin:3px 0 0;
}
.global-system-footer__links{
    display:flex;
    flex-wrap:wrap;
    justify-content:center;
    gap:18px;
}
.global-system-footer__link{
    color:#1f2937;
    font-size:12px;
    text-decoration:none;
}
.global-system-footer__link:hover{
    text-decoration:underline;
}
@media (max-width: 840px){
    .global-system-footer__inner{
        justify-content:center;
        text-align:center;
        padding:12px 16px;
    }
}
</style>
"""

GLOBAL_SYSTEM_FOOTER_HTML = """
<footer class="global-system-footer" data-global-system-footer>
    <div class="global-system-footer__inner">
        <div>
            <p class="global-system-footer__brand">AutoFill AI</p>
            <p class="global-system-footer__meta">© 2026 AutoFill AI. All rights reserved. Precision automation for enterprise.</p>
        </div>
        <nav class="global-system-footer__links" aria-label="Footer links">
            <a class="global-system-footer__link" href="#">Privacy Policy</a>
            <a class="global-system-footer__link" href="#">Terms of Service</a>
            <a class="global-system-footer__link" href="#">Security</a>
            <a class="global-system-footer__link" href="#">Status</a>
            <a class="global-system-footer__link" href="#">Contact</a>
        </nav>
    </div>
</footer>
"""


def _apply_no_store_headers(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"


def _inject_global_system_footer(html_content: str) -> str:
    if not html_content or "data-global-system-footer" in html_content:
        return html_content

    content = re.sub(r"<footer[\s\S]*?</footer>", "", html_content, flags=re.IGNORECASE)
    if "global-system-footer-style" not in content and "</head>" in content:
        content = content.replace("</head>", f"{GLOBAL_SYSTEM_FOOTER_STYLE}\n</head>", 1)
    if "</body>" in content:
        return content.replace("</body>", f"{GLOBAL_SYSTEM_FOOTER_HTML}\n</body>", 1)
    return f"{content}\n{GLOBAL_SYSTEM_FOOTER_HTML}"


@app.middleware("http")
async def apply_global_footer_middleware(request: Request, call_next):
    if request.method == "GET":
        redirect_target = STATIC_PAGE_REDIRECTS.get(request.url.path)
        if redirect_target:
            redirect_response = RedirectResponse(url=redirect_target, status_code=307)
            _apply_no_store_headers(redirect_response)
            return redirect_response

    response = await call_next(request)

    if request.url.path.startswith("/api/"):
        return response
    if request.url.path.startswith("/static/"):
        return response

    _apply_no_store_headers(response)

    content_type = response.headers.get("content-type", "")
    if "text/html" not in content_type.lower():
        return response

    body = b""
    async for chunk in response.body_iterator:
        body += chunk

    try:
        html_content = body.decode("utf-8")
    except UnicodeDecodeError:
        return Response(
            content=body,
            status_code=response.status_code,
            media_type=response.media_type,
            headers={k: v for k, v in response.headers.items() if k.lower() != "content-length"},
        )

    updated_html = _inject_global_system_footer(html_content)
    return HTMLResponse(
        content=updated_html,
        status_code=response.status_code,
        headers={k: v for k, v in response.headers.items() if k.lower() != "content-length"},
    )

# Include routers
app.include_router(auth.router)
app.include_router(suggestions.router)
app.include_router(word.router)
app.include_router(form_replacement.router)
app.include_router(excel.router)
app.include_router(composer.router)
app.include_router(form_edit.router)
app.include_router(admin.router)
app.include_router(payment.router)

# Mount static files from backend/app/static
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Mount UI files from root ui folder
ui_dir = os.path.join(os.path.dirname(__file__), "..", "..", "ui")
if os.path.exists(ui_dir):
    app.mount("/ui", StaticFiles(directory=ui_dir), name="ui")


def _redirect_to_login(_request: Request):
    return RedirectResponse(url="/", status_code=303)


def _resolve_feature_name(path: str) -> str:
    if path == "/":
        return "home"
    if path.startswith("/composer"):
        return "composer"
    if path.startswith("/word-upload"):
        return "word_upload"
    if path.startswith("/excel-data-form"):
        return "excel_data_form"
    if path.startswith("/excel-form"):
        return "excel_form"
    if path.startswith("/excel"):
        return "excel_upload"
    if path.startswith("/form"):
        return "form"
    if path.startswith("/user-account"):
        return "user_account"
    if path.startswith("/payment"):
        return "payment"
    if path.startswith("/admin-dashboard"):
        return "admin_dashboard"
    if path.startswith("/admin-users"):
        return "admin_users"
    if path.startswith("/admin-forms"):
        return "admin_forms"
    if path.startswith("/admin-reports"):
        return "admin_reports"
    if path.startswith("/admin-audit-log"):
        return "admin_audit_log"
    if path.startswith("/admin-account"):
        return "admin_account"
    return "unknown"


def _record_user_activity(
    db: Session,
    user_id: int,
    request: Request,
    activity_type: str = "page_view",
    description: str = "",
):
    """Persist per-user website activity for auditing and personal history."""
    try:
        entry = UserActivity(
            user_id=user_id,
            activity_type=activity_type,
            feature=_resolve_feature_name(request.url.path),
            path=request.url.path,
            method=request.method,
            description=description or f"User accessed {request.url.path}",
        )
        db.add(entry)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"Failed to record user activity: {e}")


def _authorize_user_ui(request: Request, db: Session):
    """Ensure logged-in users can access user functionality pages."""
    user = auth.get_authenticated_user_from_request(request, db)
    if not user:
        return None, _redirect_to_login(request)
    return user, None


def _get_optional_authenticated_user(request: Request, db: Session):
    """Return authenticated user if present, otherwise None."""
    return auth.get_authenticated_user_from_request(request, db)


def _authorize_admin_ui(request: Request, db: Session):
    """Ensure only admin users can access admin HTML pages."""
    user, auth_response = _authorize_user_ui(request, db)
    if auth_response:
        return None, auth_response
    if not user.is_admin:
        return None, RedirectResponse(url="/", status_code=303)
    return user, None


@app.get("/login", tags=["ui"])
async def login_page():
    """Serve the login page"""
    from fastapi.responses import HTMLResponse
    try:
        with open(os.path.join(static_dir, "login.html"), "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except Exception as e:
        logger.error(f"Error loading login.html: {e}")
        return HTMLResponse(content="<h1>Login page not found</h1>", status_code=404)


@app.get("/register", tags=["ui"])
async def register_page():
    """Serve the register page"""
    from fastapi.responses import HTMLResponse
    try:
        with open(os.path.join(static_dir, "register.html"), "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except Exception as e:
        logger.error(f"Error loading register.html: {e}")
        return HTMLResponse(content="<h1>Register page not found</h1>", status_code=404)


@app.get("/user-account", tags=["ui"])
async def user_account_page(request: Request, db: Session = Depends(get_db)):
    """Serve the user account management page"""
    from fastapi.responses import HTMLResponse

    user, auth_response = _authorize_user_ui(request, db)
    if auth_response:
        return auth_response

    _record_user_activity(db, user.id, request, description="Opened user account page")

    try:
        with open(os.path.join(static_dir, "user-account.html"), "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except Exception as e:
        logger.error(f"Error loading user-account.html: {e}")
        return HTMLResponse(content="<h1>User account page not found</h1>", status_code=404)


@app.get("/form", tags=["ui"])
async def form_page(request: Request, db: Session = Depends(get_db)):
    """Serve the form HTML page"""
    from fastapi.responses import HTMLResponse

    user, auth_response = _authorize_user_ui(request, db)
    if auth_response:
        return auth_response

    _record_user_activity(db, user.id, request, description="Opened form page")

    with open(os.path.join(static_dir, "form.html"), "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/excel", tags=["ui"])
async def excel_page(request: Request, db: Session = Depends(get_db)):
    """Serve the Excel upload page"""
    from fastapi.responses import HTMLResponse

    user, auth_response = _authorize_user_ui(request, db)
    if auth_response:
        return auth_response

    _record_user_activity(db, user.id, request, description="Opened Excel upload page")

    try:
        with open(os.path.join(static_dir, "excel-upload.html"), "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except Exception as e:
        logger.error(f"Error loading excel-upload.html: {e}")
        raise


@app.get("/word-upload", tags=["ui"])
async def word_upload_page(request: Request, db: Session = Depends(get_db)):
    """Serve the Word upload page"""
    from fastapi.responses import HTMLResponse

    user, auth_response = _authorize_user_ui(request, db)
    if auth_response:
        return auth_response

    _record_user_activity(db, user.id, request, description="Opened Word upload page")

    try:
        with open(os.path.join(static_dir, "word-upload.html"), "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except Exception as e:
        logger.error(f"Error loading word-upload.html: {e}")
        raise


@app.get("/excel-form/{session_id}", tags=["ui"])
async def excel_form_page(session_id: str, request: Request, db: Session = Depends(get_db)):
    """Serve the Excel form page"""
    from fastapi.responses import HTMLResponse

    user, auth_response = _authorize_user_ui(request, db)
    if auth_response:
        return auth_response

    _record_user_activity(db, user.id, request, description=f"Opened Excel form session {session_id}")

    try:
        with open(os.path.join(static_dir, "excel-form.html"), "r", encoding="utf-8") as f:
            content = f.read()
            # Inject session_id into the page
            content = content.replace("{{SESSION_ID}}", session_id)
            return HTMLResponse(content=content)
    except Exception as e:
        logger.error(f"Error loading excel-form.html: {e}")
        raise


@app.get("/excel-data-form/{session_id}", tags=["ui"])
async def excel_data_form_page(session_id: str, request: Request, db: Session = Depends(get_db)):
    """Serve the Excel data auto-fill form page"""
    from fastapi.responses import HTMLResponse

    user, auth_response = _authorize_user_ui(request, db)
    if auth_response:
        return auth_response

    _record_user_activity(db, user.id, request, description=f"Opened Excel data form session {session_id}")

    try:
        with open(os.path.join(static_dir, "excel-data-form.html"), "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except Exception as e:
        logger.error(f"Error loading excel-data-form.html: {e}")
        raise


def _load_homepage_html(is_authenticated: bool = False):
    from fastapi.responses import HTMLResponse

    landing_v2_path = os.path.join(static_dir, "interfaces", "zephyr-landing", "index.html")
    menu_path = os.path.join(static_dir, "menu.html")

    # Authenticated users land on the full feature menu.
    if is_authenticated:
        try:
            with open(menu_path, "r", encoding="utf-8") as f:
                return HTMLResponse(content=f.read())
        except Exception as e:
            logger.error(f"Error loading authenticated menu.html: {e}")

    try:
        with open(landing_v2_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except Exception as e:
        logger.error(f"Error loading landing v2 index.html: {e}")

    # Fallback to legacy homepage if the new landing is not available.
    try:
        with open(menu_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except Exception as e:
        logger.error(f"Error loading fallback menu.html: {e}")
        return {
            "message": "AutoFill AI System API",
            "version": "1.0.0",
            "status": "running",
            "menu": "/static/menu.html"
        }


@app.get("/", tags=["ui"])
async def root(request: Request, db: Session = Depends(get_db)):
    """Root endpoint - landing for guests, full menu for authenticated users."""
    user = _get_optional_authenticated_user(request, db)
    return _load_homepage_html(is_authenticated=bool(user))


@app.get("/home", tags=["ui"])
async def home_page(request: Request, db: Session = Depends(get_db)):
    """Homepage alias with auth-aware UI."""
    user = _get_optional_authenticated_user(request, db)
    return _load_homepage_html(is_authenticated=bool(user))

@app.get("/composer", tags=["ui"])
async def composer_page(request: Request, db: Session = Depends(get_db)):
    """Serve the document composer page"""
    from fastapi.responses import HTMLResponse

    user, auth_response = _authorize_user_ui(request, db)
    if auth_response:
        return auth_response

    _record_user_activity(db, user.id, request, description="Opened document composer")

    try:
        with open(os.path.join(static_dir, "composer.html"), "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except Exception as e:
        logger.error(f"Error loading composer.html: {e}")
        raise


@app.get("/payment", tags=["ui"])
async def payment_page(request: Request, db: Session = Depends(get_db)):
    """Serve the payment and upgrade plans page"""
    from fastapi.responses import HTMLResponse

    user, auth_response = _authorize_user_ui(request, db)
    if auth_response:
        return auth_response

    _record_user_activity(db, user.id, request, description="Opened payment page")

    try:
        with open(os.path.join(static_dir, "payment.html"), "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except Exception as e:
        logger.error(f"Error loading payment.html: {e}")
        return HTMLResponse(content="<h1>Payment page not found</h1>", status_code=404)


# ==================== ADMIN PAGES ====================

@app.get("/admin-dashboard", tags=["ui"])
async def admin_dashboard_page(request: Request, db: Session = Depends(get_db)):
    """Serve the admin dashboard page"""
    from fastapi.responses import HTMLResponse

    user, auth_response = _authorize_admin_ui(request, db)
    if auth_response:
        return auth_response

    _record_user_activity(db, user.id, request, activity_type="feature_access", description="Opened admin dashboard")

    try:
        with open(os.path.join(static_dir, "admin-dashboard.html"), "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except Exception as e:
        logger.error(f"Error loading admin-dashboard.html: {e}")
        return HTMLResponse(content="<h1>Admin dashboard page not found</h1>", status_code=404)


@app.get("/admin-users", tags=["ui"])
async def admin_users_page(request: Request, db: Session = Depends(get_db)):
    """Serve the admin users management page"""
    from fastapi.responses import HTMLResponse

    user, auth_response = _authorize_admin_ui(request, db)
    if auth_response:
        return auth_response

    _record_user_activity(db, user.id, request, activity_type="feature_access", description="Opened admin users")

    try:
        with open(os.path.join(static_dir, "admin-users.html"), "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except Exception as e:
        logger.error(f"Error loading admin-users.html: {e}")
        return HTMLResponse(content="<h1>Admin users page not found</h1>", status_code=404)


@app.get("/admin-forms", tags=["ui"])
async def admin_forms_page(request: Request, db: Session = Depends(get_db)):
    """Serve the admin forms management page"""
    from fastapi.responses import HTMLResponse

    user, auth_response = _authorize_admin_ui(request, db)
    if auth_response:
        return auth_response

    _record_user_activity(db, user.id, request, activity_type="feature_access", description="Opened admin forms")

    try:
        with open(os.path.join(static_dir, "admin-forms.html"), "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except Exception as e:
        logger.error(f"Error loading admin-forms.html: {e}")
        return HTMLResponse(content="<h1>Admin forms page not found</h1>", status_code=404)


@app.get("/admin-reports", tags=["ui"])
async def admin_reports_page(request: Request, db: Session = Depends(get_db)):
    """Serve the admin reports page"""
    from fastapi.responses import HTMLResponse

    user, auth_response = _authorize_admin_ui(request, db)
    if auth_response:
        return auth_response

    _record_user_activity(db, user.id, request, activity_type="feature_access", description="Opened admin reports")

    try:
        with open(os.path.join(static_dir, "admin-reports.html"), "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except Exception as e:
        logger.error(f"Error loading admin-reports.html: {e}")
        return HTMLResponse(content="<h1>Admin reports page not found</h1>", status_code=404)


@app.get("/admin-audit-log", tags=["ui"])
async def admin_audit_log_page(request: Request, db: Session = Depends(get_db)):
    """Serve the admin audit log page"""
    from fastapi.responses import HTMLResponse

    user, auth_response = _authorize_admin_ui(request, db)
    if auth_response:
        return auth_response

    _record_user_activity(db, user.id, request, activity_type="feature_access", description="Opened admin audit log")

    try:
        with open(os.path.join(static_dir, "admin-audit-log.html"), "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except Exception as e:
        logger.error(f"Error loading admin-audit-log.html: {e}")
        return HTMLResponse(content="<h1>Admin audit log page not found</h1>", status_code=404)


@app.get("/admin-account", tags=["ui"])
async def admin_account_page(request: Request, db: Session = Depends(get_db)):
    """Serve the admin account settings page"""
    from fastapi.responses import HTMLResponse

    user, auth_response = _authorize_admin_ui(request, db)
    if auth_response:
        return auth_response

    _record_user_activity(db, user.id, request, activity_type="feature_access", description="Opened admin account")

    try:
        with open(os.path.join(static_dir, "admin-account.html"), "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except Exception as e:
        logger.error(f"Error loading admin-account.html: {e}")
        return HTMLResponse(content="<h1>Admin account page not found</h1>", status_code=404)

@app.get("/health", tags=["health"])
async def health():
    """Health check endpoint"""
    logger.info("Health check called")
    return {
        "status": "ok",
        "service": "autofill-ai-system"
    }


@app.get("/api/v1", tags=["info"])
async def api_info():
    """API info endpoint"""
    logger.info("API info called")
    return {
        "name": settings.PROJECT_NAME,
        "version": "1.0.0",
        "endpoints": {
            "suggestions": "/api/suggestions",
            "suggestions_history": "/api/suggestions/history",
            "field_stats": "/api/suggestions/stats"
        }
    }


if __name__ == "__main__":
    import uvicorn
    logger.info(f"Starting {settings.PROJECT_NAME} server...")
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
