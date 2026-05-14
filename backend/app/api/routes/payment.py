"""
Payment routes: plan listing, SePay checkout, webhook handling.
"""
from datetime import datetime, timedelta
import hashlib
import hmac
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db.models import PaymentOrder, User, UserSubscription
from app.db.session import get_db
from app.api.routes.auth import get_authenticated_user_from_request

router = APIRouter(prefix="/api/payments", tags=["payments"])


PLAN_DEFINITIONS = [
    {
        "code": "basic_monthly",
        "name": "Cơ bản",
        "price_vnd": 3000,
        "duration_days": 30,
        "description": "Phù hợp cho cá nhân bắt đầu tự động hóa biểu mẫu.",
        "features": [
            "50 tiến trình Word/Excel mỗi tháng",
            "Mapping AI cơ bản cho biểu mẫu",
            "Lưu lịch sử theo tài khoản cá nhân",
            "Hỗ trợ tiêu chuẩn",
        ],
    },
    {
        "code": "professional_monthly",
        "name": "Chuyên nghiệp",
        "price_vnd": 10000,
        "duration_days": 30,
        "description": "Dành cho đội ngũ vận hành cần năng suất cao mỗi ngày.",
        "features": [
            "Không giới hạn tiến trình Word/Excel",
            "AI Composer nâng cao theo ngữ cảnh",
            "Gợi ý tự động và lịch sử thông minh",
            "Hỗ trợ ưu tiên 24/7",
            "Truy cập API đầy đủ",
        ],
    },
    {
        "code": "enterprise_monthly",
        "name": "Doanh nghiệp",
        "price_vnd": 30000,
        "duration_days": 30,
        "description": "Giải pháp cho doanh nghiệp có quy trình tài liệu phức tạp.",
        "features": [
            "Tích hợp quy trình và mapping tùy chỉnh",
            "Quản lý tài khoản và phân quyền mở rộng",
            "Bảo mật cấp doanh nghiệp",
            "Tùy chọn triển khai On-premise",
            "Đội kỹ thuật đồng hành theo nhu cầu",
        ],
    },
]


def _get_plan(plan_code: str) -> Optional[Dict]:
    return next((plan for plan in PLAN_DEFINITIONS if plan["code"] == plan_code), None)


def _safe_iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _now_utc() -> datetime:
    return datetime.utcnow()


def _now_vn() -> datetime:
    return _now_utc() + timedelta(hours=7)


def _get_payment_gateway() -> str:
    gateway = os.getenv("PAYMENT_GATEWAY", "vnpay").strip().lower()
    if gateway not in {"vnpay", "sepay"}:
        return "vnpay"
    return gateway


def _new_order_code(user_id: int) -> str:
    nonce = uuid.uuid4().hex[:8].upper()
    return f"AF{user_id}{int(_now_utc().timestamp())}{nonce}"


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _extract_first(payload: Dict, keys: list[str], default=None):
    for key in keys:
        if payload.get(key) is not None:
            return payload.get(key)
    return default


def _extract_nested_dict(payload: Dict, keys: list[str]) -> Dict:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _format_order_payload(order: PaymentOrder) -> Dict:
    return {
        "order_code": order.order_code,
        "plan_code": order.plan_code,
        "plan_name": order.plan_name,
        "amount_vnd": order.amount_vnd,
        "status": order.status,
        "paid_at": _safe_iso(order.paid_at),
        "expires_at": _safe_iso(order.expires_at),
        "checkout_url": order.sepay_checkout_url,
        "qr_url": order.sepay_qr_url,
    }


def _expire_stale_subscriptions(db: Session, user_id: int) -> None:
    now = _now_utc()
    stale_rows = (
        db.query(UserSubscription)
        .filter(
            UserSubscription.user_id == user_id,
            UserSubscription.status == "active",
            UserSubscription.expires_at < now,
        )
        .all()
    )
    if not stale_rows:
        return
    for row in stale_rows:
        row.status = "expired"
    db.commit()


def _get_active_subscription(db: Session, user_id: int) -> Optional[UserSubscription]:
    _expire_stale_subscriptions(db, user_id)
    now = _now_utc()
    return (
        db.query(UserSubscription)
        .filter(
            UserSubscription.user_id == user_id,
            UserSubscription.status == "active",
            UserSubscription.expires_at >= now,
        )
        .order_by(UserSubscription.expires_at.desc())
        .first()
    )


def _activate_subscription_from_order(db: Session, order: PaymentOrder) -> UserSubscription:
    now = _now_utc()
    active_subscription = _get_active_subscription(db, order.user_id)
    start_at = now
    if active_subscription and active_subscription.expires_at > now:
        start_at = active_subscription.expires_at
        active_subscription.status = "expired"

    plan = _get_plan(order.plan_code)
    duration_days = plan["duration_days"] if plan else 30
    expires_at = start_at + timedelta(days=duration_days)
    new_subscription = UserSubscription(
        user_id=order.user_id,
        plan_code=order.plan_code,
        plan_name=order.plan_name,
        amount_vnd=order.amount_vnd,
        duration_days=duration_days,
        status="active",
        started_at=start_at,
        expires_at=expires_at,
    )
    db.add(new_subscription)
    db.commit()
    db.refresh(new_subscription)
    return new_subscription


def _format_subscription_payload(subscription: Optional[UserSubscription]) -> Optional[Dict]:
    if not subscription:
        return None
    return {
        "plan_code": subscription.plan_code,
        "plan_name": subscription.plan_name,
        "amount_vnd": subscription.amount_vnd,
        "duration_days": subscription.duration_days,
        "status": subscription.status,
        "started_at": _safe_iso(subscription.started_at),
        "expires_at": _safe_iso(subscription.expires_at),
    }


def _call_sepay_create_order(order: PaymentOrder, user: User, return_url: Optional[str]) -> Dict:
    api_key = os.getenv("SEPAY_API_KEY", "").strip()
    endpoint = os.getenv("SEPAY_CREATE_ORDER_URL", "").strip()
    webhook_url = os.getenv("SEPAY_WEBHOOK_URL", "").strip()

    if not api_key or not endpoint:
        raise HTTPException(
            status_code=503,
            detail="SePay chưa được cấu hình. Vui lòng thiết lập SEPAY_API_KEY và SEPAY_CREATE_ORDER_URL.",
        )

    payload = {
        "order_code": order.order_code,
        "amount": order.amount_vnd,
        "currency": "VND",
        "description": order.customer_note or f"Thanh toán gói {order.plan_name}",
        "customer_email": user.email,
        "customer_name": user.username,
        "return_url": return_url or "",
        "webhook_url": webhook_url or "",
    }

    request_body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=request_body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            raw_text = response.read().decode("utf-8")
            if not raw_text:
                raise HTTPException(status_code=502, detail="SePay không trả dữ liệu cho yêu cầu tạo thanh toán.")
            data = json.loads(raw_text)
            if not isinstance(data, dict):
                raise HTTPException(status_code=502, detail="Dữ liệu phản hồi từ SePay không hợp lệ.")
            return data
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8") if hasattr(e, "read") else str(e)
        raise HTTPException(status_code=502, detail=f"SePay phản hồi lỗi: {detail}")
    except urllib.error.URLError as e:
        raise HTTPException(status_code=502, detail=f"Không kết nối được SePay: {str(e)}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=502, detail="Dữ liệu phản hồi từ SePay không hợp lệ.")


def _build_vnpay_query(params: Dict[str, str]) -> str:
    sorted_items = sorted((str(key), str(value)) for key, value in params.items())
    return urllib.parse.urlencode(sorted_items, quote_via=urllib.parse.quote_plus)


def _sign_vnpay(params: Dict[str, str], secret: str) -> str:
    signing_data = _build_vnpay_query(params)
    digest = hmac.new(secret.encode("utf-8"), signing_data.encode("utf-8"), hashlib.sha512).hexdigest()
    return digest


def _resolve_vnpay_return_url(return_url: Optional[str]) -> str:
    configured_return_url = os.getenv("VNPAY_RETURN_URL", "").strip()
    if configured_return_url:
        return configured_return_url
    if return_url and return_url.startswith(("http://", "https://")):
        return return_url
    raise HTTPException(
        status_code=503,
        detail="VNPay chưa có URL trả về hợp lệ. Hãy cấu hình VNPAY_RETURN_URL.",
    )


def _extract_client_ip(request: Optional[Request]) -> str:
    forwarded_for = ""
    if request:
        forwarded_for = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if forwarded_for:
        return forwarded_for
    if request and request.client and request.client.host:
        return str(request.client.host)
    return "127.0.0.1"


def _call_vnpay_create_order(order: PaymentOrder, user: User, request: Request, return_url: Optional[str]) -> Dict:
    tmn_code = os.getenv("VNPAY_TMN_CODE", "").strip()
    hash_secret = os.getenv("VNPAY_HASH_SECRET", "").strip()
    payment_url = os.getenv("VNPAY_PAYMENT_URL", "https://sandbox.vnpayment.vn/paymentv2/vpcpay.html").strip()

    if not tmn_code or not hash_secret or not payment_url:
        raise HTTPException(
            status_code=503,
            detail="VNPay chưa được cấu hình đầy đủ. Cần VNPAY_TMN_CODE, VNPAY_HASH_SECRET, VNPAY_PAYMENT_URL.",
        )

    client_ip = _extract_client_ip(request)
    create_time = _now_vn()
    expire_time = create_time + timedelta(minutes=20)
    locale = os.getenv("VNPAY_LOCALE", "vn").strip() or "vn"
    order_type = os.getenv("VNPAY_ORDER_TYPE", "other").strip() or "other"
    bank_code = os.getenv("VNPAY_BANK_CODE", "").strip()
    note = (order.customer_note or f"Thanh toan goi {order.plan_name} - {user.username}")[:255]

    params = {
        "vnp_Version": "2.1.0",
        "vnp_Command": "pay",
        "vnp_TmnCode": tmn_code,
        "vnp_Amount": str(int(order.amount_vnd) * 100),
        "vnp_CreateDate": create_time.strftime("%Y%m%d%H%M%S"),
        "vnp_CurrCode": "VND",
        "vnp_IpAddr": client_ip,
        "vnp_Locale": locale,
        "vnp_OrderInfo": note,
        "vnp_OrderType": order_type,
        "vnp_ReturnUrl": _resolve_vnpay_return_url(return_url),
        "vnp_TxnRef": order.order_code,
        "vnp_ExpireDate": expire_time.strftime("%Y%m%d%H%M%S"),
    }
    if bank_code:
        params["vnp_BankCode"] = bank_code

    secure_hash = _sign_vnpay(params, hash_secret)
    query_string = _build_vnpay_query(params)
    checkout_url = f"{payment_url}?{query_string}&vnp_SecureHash={secure_hash}"
    qr_url = f"https://quickchart.io/qr?size=320&text={urllib.parse.quote(checkout_url, safe='')}"

    return {
        "status": "pending",
        "gateway": "vnpay",
        "checkout_url": checkout_url,
        "qr_url": qr_url,
    }


def _build_manual_payment_response(order: PaymentOrder, reason: str = "") -> Dict:
    bank_code = os.getenv("SEPAY_BANK_CODE", "").strip()
    account_number = os.getenv("SEPAY_BANK_ACCOUNT", "").strip()
    account_name = os.getenv("SEPAY_BANK_ACCOUNT_NAME", "").strip()
    transfer_content = order.order_code

    checkout_url = ""
    qr_url = ""
    if bank_code and account_number:
        query = urllib.parse.urlencode(
            {
                "amount": order.amount_vnd,
                "addInfo": transfer_content,
                "accountName": account_name,
            },
            quote_via=urllib.parse.quote,
        )
        qr_url = f"https://img.vietqr.io/image/{bank_code}-{account_number}-compact2.png?{query}"
        checkout_url = qr_url
    else:
        fallback_text = (
            f"Thanh toan AutoFill AI | Order {order.order_code} | So tien {order.amount_vnd} VND"
        )
        encoded = urllib.parse.quote(fallback_text, safe="")
        qr_url = f"https://quickchart.io/qr?size=320&text={encoded}"

    return {
        "status": "pending",
        "checkout_url": checkout_url,
        "qr_url": qr_url,
        "fallback_reason": reason or "manual_fallback",
    }


def _extract_order_code(payload: Dict) -> Optional[str]:
    direct_candidates = [
        payload.get("order_code"),
        payload.get("orderCode"),
        payload.get("order_id"),
        payload.get("orderId"),
    ]
    for value in direct_candidates:
        if value:
            return str(value)

    note = str(payload.get("description") or payload.get("content") or payload.get("transfer_content") or "")
    match = re.search(r"AF\d{6,}", note)
    return match.group(0) if match else None


def _is_paid_status(payload: Dict) -> bool:
    status_text = str(
        payload.get("status")
        or payload.get("payment_status")
        or payload.get("transaction_status")
        or ""
    ).lower()
    success_states = {"paid", "success", "succeeded", "completed", "done"}
    return status_text in success_states


def _is_failed_status(payload: Dict) -> bool:
    status_text = str(
        payload.get("status")
        or payload.get("payment_status")
        or payload.get("transaction_status")
        or ""
    ).lower()
    failed_states = {"failed", "cancelled", "canceled", "error", "declined"}
    return status_text in failed_states


def _is_vnpay_paid(payload: Dict) -> bool:
    response_code = str(payload.get("vnp_ResponseCode") or payload.get("response_code") or "").strip()
    transaction_status = str(payload.get("vnp_TransactionStatus") or payload.get("transaction_status") or "").strip()
    if response_code != "00":
        return False
    return transaction_status in {"", "00", "0"}


def _is_vnpay_failed(payload: Dict) -> bool:
    response_code = str(payload.get("vnp_ResponseCode") or payload.get("response_code") or "").strip()
    transaction_status = str(payload.get("vnp_TransactionStatus") or payload.get("transaction_status") or "").strip()
    failed_status = {"02", "03", "04", "05", "07", "08", "09", "11", "12", "13", "24", "51", "65", "75", "79", "99"}
    if response_code and response_code != "00":
        return True
    return transaction_status in failed_status


def _verify_vnpay_signature(payload: Dict) -> bool:
    hash_secret = os.getenv("VNPAY_HASH_SECRET", "").strip()
    if not hash_secret:
        return False

    provided_hash = str(payload.get("vnp_SecureHash") or "").strip()
    if not provided_hash:
        return False

    sign_payload = {
        key: value
        for key, value in payload.items()
        if key not in {"vnp_SecureHash", "vnp_SecureHashType"}
    }
    expected_hash = _sign_vnpay(sign_payload, hash_secret)
    return hmac.compare_digest(provided_hash.lower(), expected_hash.lower())


def _mark_order_paid(db: Session, order: PaymentOrder, transaction_id: Optional[str] = None) -> None:
    was_paid = order.status == "paid"
    order.status = "paid"
    order.paid_at = order.paid_at or _now_utc()
    if transaction_id:
        order.sepay_transaction_id = transaction_id
    db.commit()
    if not was_paid:
        _activate_subscription_from_order(db, order)


def _detect_order_gateway(order: PaymentOrder) -> str:
    if order.raw_response:
        try:
            payload = json.loads(order.raw_response)
            if isinstance(payload, dict):
                gateway = str(payload.get("gateway") or "").strip().lower()
                if gateway in {"vnpay", "sepay"}:
                    return gateway
        except Exception:
            pass

    checkout_url = (order.sepay_checkout_url or "").lower()
    if "vnpay" in checkout_url or "vnpayment" in checkout_url:
        return "vnpay"
    return _get_payment_gateway()


def _sync_order_status_from_sepay(db: Session, order: PaymentOrder) -> None:
    if order.status != "pending":
        return

    api_key = os.getenv("SEPAY_API_KEY", "").strip()
    endpoint = os.getenv("SEPAY_ORDER_STATUS_URL", "").strip()
    if not api_key or not endpoint:
        return

    status_url = f"{endpoint}?order_code={order.order_code}"
    req = urllib.request.Request(
        status_url,
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            raw_text = response.read().decode("utf-8")
            payload = json.loads(raw_text) if raw_text else {}
    except Exception:
        return

    order.raw_response = json.dumps(payload, ensure_ascii=False)
    if _is_paid_status(payload):
        tx_id = str(
            payload.get("transaction_id") or payload.get("transactionId") or order.sepay_transaction_id or ""
        ) or None
        _mark_order_paid(db, order, transaction_id=tx_id)
        return
    elif _is_failed_status(payload):
        order.status = "failed"
    elif order.expires_at and order.expires_at < _now_utc():
        order.status = "expired"
    db.commit()


def _sync_order_status_from_vnpay(db: Session, order: PaymentOrder) -> None:
    if order.status != "pending":
        return

    if order.expires_at and order.expires_at < _now_utc():
        order.status = "expired"
        db.commit()


def _sync_pending_order_status(db: Session, order: PaymentOrder) -> None:
    gateway = _detect_order_gateway(order)
    if gateway == "vnpay":
        _sync_order_status_from_vnpay(db, order)
        return
    _sync_order_status_from_sepay(db, order)


def _get_latest_order(db: Session, user_id: int, plan_code: Optional[str] = None) -> Optional[PaymentOrder]:
    query = db.query(PaymentOrder).filter(PaymentOrder.user_id == user_id)
    if plan_code:
        query = query.filter(PaymentOrder.plan_code == plan_code)
    return query.order_by(PaymentOrder.created_at.desc()).first()


def _get_reusable_pending_order(db: Session, user_id: int, plan_code: str) -> Optional[PaymentOrder]:
    order = (
        db.query(PaymentOrder)
        .filter(
            PaymentOrder.user_id == user_id,
            PaymentOrder.plan_code == plan_code,
            PaymentOrder.status == "pending",
        )
        .order_by(PaymentOrder.created_at.desc())
        .first()
    )
    if not order:
        return None
    if order.expires_at and order.expires_at < _now_utc():
        order.status = "expired"
        db.commit()
        return None
    return order


def _apply_sepay_order_response(order: PaymentOrder, sepay_response: Dict) -> None:
    data_block = _extract_nested_dict(sepay_response, ["data", "result", "order"])
    metadata_block = _extract_nested_dict(sepay_response, ["metadata", "payment_info", "paymentInfo"])

    order.sepay_checkout_url = _extract_first(
        sepay_response,
        ["checkout_url", "payment_url", "payUrl", "checkoutUrl"],
        _extract_first(
            data_block,
            ["checkout_url", "payment_url", "payUrl", "checkoutUrl", "paymentLink"],
            _extract_first(metadata_block, ["checkout_url", "payment_url", "payUrl", "checkoutUrl", "paymentLink"]),
        ),
    )
    order.sepay_qr_url = _extract_first(
        sepay_response,
        ["qr_url", "qr_image", "qrImage", "qrCode", "qr_link", "qrLink"],
        _extract_first(
            data_block,
            ["qr_url", "qr_image", "qrImage", "qrCode", "qr_link", "qrLink"],
            _extract_first(metadata_block, ["qr_url", "qr_image", "qrImage", "qrCode", "qr_link", "qrLink"]),
        ),
    )

    if not order.sepay_qr_url:
        bank_code = _extract_first(
            sepay_response,
            ["bank_code", "bankCode", "bank_short_name", "bankShortName", "bank_bin", "bankBin", "bin"],
            _extract_first(
                data_block,
                ["bank_code", "bankCode", "bank_short_name", "bankShortName", "bank_bin", "bankBin", "bin"],
                _extract_first(
                    metadata_block,
                    ["bank_code", "bankCode", "bank_short_name", "bankShortName", "bank_bin", "bankBin", "bin"],
                ),
            ),
        ) or os.getenv("SEPAY_BANK_CODE", "").strip()

        account_number = _extract_first(
            sepay_response,
            ["account_number", "accountNumber", "bank_account", "bankAccount", "account_no", "accountNo"],
            _extract_first(
                data_block,
                ["account_number", "accountNumber", "bank_account", "bankAccount", "account_no", "accountNo"],
                _extract_first(
                    metadata_block,
                    ["account_number", "accountNumber", "bank_account", "bankAccount", "account_no", "accountNo"],
                ),
            ),
        ) or os.getenv("SEPAY_BANK_ACCOUNT", "").strip()

        account_name = _extract_first(
            sepay_response,
            ["account_name", "accountName", "bank_account_name", "bankAccountName"],
            _extract_first(
                data_block,
                ["account_name", "accountName", "bank_account_name", "bankAccountName"],
                _extract_first(metadata_block, ["account_name", "accountName", "bank_account_name", "bankAccountName"]),
            ),
        ) or os.getenv("SEPAY_BANK_ACCOUNT_NAME", "").strip()

        transfer_content = _extract_first(
            sepay_response,
            ["transfer_content", "transferContent", "content", "description"],
            _extract_first(
                data_block,
                ["transfer_content", "transferContent", "content", "description"],
                _extract_first(metadata_block, ["transfer_content", "transferContent", "content", "description"]),
            ),
        ) or order.order_code

        if bank_code and account_number:
            query = urllib.parse.urlencode(
                {
                    "amount": order.amount_vnd,
                    "addInfo": transfer_content,
                    "accountName": account_name,
                },
                quote_via=urllib.parse.quote,
            )
            order.sepay_qr_url = f"https://img.vietqr.io/image/{bank_code}-{account_number}-compact2.png?{query}"
        elif order.sepay_checkout_url:
            encoded_checkout = urllib.parse.quote(order.sepay_checkout_url, safe="")
            order.sepay_qr_url = f"https://quickchart.io/qr?size=320&text={encoded_checkout}"

    order.raw_response = json.dumps(sepay_response, ensure_ascii=False)

    if _is_paid_status(sepay_response):
        order.status = "paid"
        order.paid_at = order.paid_at or _now_utc()
        tx_id = _extract_first(
            sepay_response,
            ["transaction_id", "transactionId", "id"],
            _extract_first(data_block, ["transaction_id", "transactionId", "id"]),
        )
        order.sepay_transaction_id = str(tx_id) if tx_id else order.sepay_transaction_id
    elif _is_failed_status(sepay_response):
        order.status = "failed"


@router.get("/plans")
async def list_plans(request: Request, db: Session = Depends(get_db)):
    user = get_authenticated_user_from_request(request, db)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Bạn chưa đăng nhập"})

    active_subscription = _get_active_subscription(db, user.id)
    latest_order = _get_latest_order(db, user.id)
    if latest_order and latest_order.status == "pending":
        _sync_pending_order_status(db, latest_order)
        db.refresh(latest_order)
        if latest_order.status == "pending" and latest_order.expires_at and latest_order.expires_at < _now_utc():
            latest_order.status = "expired"
            db.commit()
            db.refresh(latest_order)

    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "plans": PLAN_DEFINITIONS,
            "active_subscription": _format_subscription_payload(active_subscription),
            "latest_order": _format_order_payload(latest_order) if latest_order else None,
        },
    )


@router.post("/create-order")
async def create_order(request: Request, db: Session = Depends(get_db)):
    user = get_authenticated_user_from_request(request, db)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Bạn chưa đăng nhập"})

    data = await request.json()
    plan_code = (data.get("plan_code") or "").strip()
    return_url = (data.get("return_url") or "").strip()

    selected_plan = _get_plan(plan_code)
    if not selected_plan:
        return JSONResponse(status_code=400, content={"error": "Gói nâng cấp không hợp lệ"})

    existing_order = _get_reusable_pending_order(db, user.id, selected_plan["code"])
    if existing_order:
        if existing_order.status == "pending":
            _sync_pending_order_status(db, existing_order)
            db.refresh(existing_order)
        if existing_order.status == "pending":
            return JSONResponse(
                status_code=200,
                content={
                    "success": True,
                    "message": "Đang sử dụng đơn chờ thanh toán trước đó.",
                    "order": _format_order_payload(existing_order),
                },
            )

    now = _now_utc()
    order_code = _new_order_code(user.id)
    order = PaymentOrder(
        user_id=user.id,
        order_code=order_code,
        plan_code=selected_plan["code"],
        plan_name=selected_plan["name"],
        amount_vnd=int(selected_plan["price_vnd"]),
        status="pending",
        customer_note=f"Thanh toán gói {selected_plan['name']} cho {user.username}",
        expires_at=now + timedelta(minutes=20),
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    preferred_gateway = _get_payment_gateway()
    active_gateway = preferred_gateway
    try:
        gateway_response = {}
        if preferred_gateway == "vnpay":
            gateway_response = _call_vnpay_create_order(order, user, request, return_url)
        else:
            gateway_response = _call_sepay_create_order(order, user, return_url)
        _apply_sepay_order_response(order, gateway_response)
        db.commit()
    except HTTPException as exc:
        if exc.status_code in (502, 503):
            # Nếu gateway ưu tiên lỗi, thử fallback sang gateway còn lại trước khi chuyển QR dự phòng.
            if preferred_gateway == "vnpay":
                try:
                    sepay_response = _call_sepay_create_order(order, user, return_url)
                    _apply_sepay_order_response(order, sepay_response)
                    db.commit()
                    active_gateway = "sepay"
                except HTTPException:
                    fallback_response = _build_manual_payment_response(order, reason=str(exc.detail))
                    _apply_sepay_order_response(order, fallback_response)
                    db.commit()
                    return JSONResponse(
                        status_code=200,
                        content={
                            "success": True,
                            "message": "VNPay chưa sẵn sàng, hệ thống đã chuyển sang QR dự phòng.",
                            "order": _format_order_payload(order),
                        },
                    )
            else:
                fallback_response = _build_manual_payment_response(order, reason=str(exc.detail))
                _apply_sepay_order_response(order, fallback_response)
                db.commit()
                return JSONResponse(
                    status_code=200,
                    content={
                        "success": True,
                        "message": "Hệ thống thanh toán trực tuyến đang tạm gián đoạn, đã chuyển sang chế độ QR dự phòng.",
                        "order": _format_order_payload(order),
                    },
                )
        else:
            order.status = "failed"
            order.raw_response = json.dumps({"error": exc.detail, "gateway": active_gateway}, ensure_ascii=False)
            db.commit()
            return JSONResponse(status_code=exc.status_code, content={"error": exc.detail, "order_code": order.order_code})

    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "gateway": active_gateway,
            "order": _format_order_payload(order),
        },
    )


@router.get("/orders/latest")
async def get_latest_order(request: Request, db: Session = Depends(get_db)):
    user = get_authenticated_user_from_request(request, db)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Bạn chưa đăng nhập"})

    plan_code = (request.query_params.get("plan_code") or "").strip() or None
    order = _get_latest_order(db, user.id, plan_code=plan_code)
    if not order:
        return JSONResponse(status_code=200, content={"success": True, "order": None})

    if order.status == "pending":
        _sync_pending_order_status(db, order)
        db.refresh(order)
    if order.status == "pending" and order.expires_at and order.expires_at < _now_utc():
        order.status = "expired"
        db.commit()
        db.refresh(order)

    return JSONResponse(status_code=200, content={"success": True, "order": _format_order_payload(order)})


@router.get("/orders/{order_code}")
async def get_order_status(order_code: str, request: Request, db: Session = Depends(get_db)):
    user = get_authenticated_user_from_request(request, db)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Bạn chưa đăng nhập"})

    order = (
        db.query(PaymentOrder)
        .filter(
            PaymentOrder.order_code == order_code,
            PaymentOrder.user_id == user.id,
        )
        .first()
    )
    if not order:
        return JSONResponse(status_code=404, content={"error": "Không tìm thấy đơn thanh toán"})

    if order.status == "pending":
        _sync_pending_order_status(db, order)
        db.refresh(order)

    if order.status == "pending" and order.expires_at and order.expires_at < _now_utc():
        order.status = "expired"
        db.commit()
        db.refresh(order)

    active_subscription = _get_active_subscription(db, user.id)
    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "order": _format_order_payload(order),
            "active_subscription": _format_subscription_payload(active_subscription),
        },
    )


def _apply_vnpay_callback(db: Session, payload: Dict) -> tuple[bool, Optional[str]]:
    order_code = str(payload.get("vnp_TxnRef") or "").strip()
    if not order_code:
        return False, None

    order = db.query(PaymentOrder).filter(PaymentOrder.order_code == order_code).first()
    if not order:
        return False, order_code

    order.raw_response = json.dumps({"gateway": "vnpay", "payload": payload}, ensure_ascii=False)
    if _is_vnpay_paid(payload):
        tx_id = str(payload.get("vnp_TransactionNo") or payload.get("vnp_BankTranNo") or "").strip() or None
        _mark_order_paid(db, order, transaction_id=tx_id)
        return True, order_code

    if _is_vnpay_failed(payload) and order.status == "pending":
        order.status = "failed"
        db.commit()
    else:
        db.commit()
    return False, order_code


@router.get("/vnpay/return")
async def vnpay_return(request: Request, db: Session = Depends(get_db)):
    payload = dict(request.query_params)
    if not _verify_vnpay_signature(payload):
        return RedirectResponse(url="/payment?vnpay_status=invalid-signature", status_code=302)

    paid, order_code = _apply_vnpay_callback(db, payload)
    payment_status = "paid" if paid else "failed"
    encoded_order_code = urllib.parse.quote(order_code or "", safe="")
    return RedirectResponse(
        url=f"/payment?vnpay_status={payment_status}&order_code={encoded_order_code}",
        status_code=302,
    )


@router.get("/vnpay/ipn")
async def vnpay_ipn(request: Request, db: Session = Depends(get_db)):
    payload = dict(request.query_params)
    if not _verify_vnpay_signature(payload):
        return JSONResponse(status_code=200, content={"RspCode": "97", "Message": "Invalid signature"})

    paid, order_code = _apply_vnpay_callback(db, payload)
    if order_code is None:
        return JSONResponse(status_code=200, content={"RspCode": "01", "Message": "Order not found"})
    if paid:
        return JSONResponse(status_code=200, content={"RspCode": "00", "Message": "Confirm Success"})
    return JSONResponse(status_code=200, content={"RspCode": "00", "Message": "Payment not completed"})


@router.post("/sepay/webhook")
async def sepay_webhook(request: Request, db: Session = Depends(get_db)):
    webhook_secret = os.getenv("SEPAY_WEBHOOK_SECRET", "").strip()
    incoming_secret = (
        request.headers.get("x-sepay-signature")
        or request.headers.get("x-sepay-token")
        or request.headers.get("authorization")
        or ""
    ).replace("Bearer ", "").strip()

    if webhook_secret and incoming_secret != webhook_secret:
        raise HTTPException(status_code=401, detail="Webhook secret không hợp lệ")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Webhook body không hợp lệ")

    order_code = _extract_order_code(payload if isinstance(payload, dict) else {})
    if not order_code:
        return JSONResponse(status_code=400, content={"success": False, "error": "Thiếu order_code"})

    order = db.query(PaymentOrder).filter(PaymentOrder.order_code == order_code).first()
    if not order:
        return JSONResponse(status_code=404, content={"success": False, "error": "Order không tồn tại"})

    order.raw_response = json.dumps({"gateway": "sepay", "payload": payload}, ensure_ascii=False)
    if _is_paid_status(payload):
        tx_id = str(
            payload.get("transaction_id") or payload.get("transactionId") or payload.get("id") or ""
        ) or None
        _mark_order_paid(db, order, transaction_id=tx_id)
        return JSONResponse(status_code=200, content={"success": True})
    elif _is_failed_status(payload):
        order.status = "failed"
    db.commit()

    return JSONResponse(status_code=200, content={"success": True})
