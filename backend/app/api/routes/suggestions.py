from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_
import re
import unicodedata
import json
from collections import defaultdict

from app.db.session import get_db
from app.db.models import Field, Entry, Suggestion, User, WordSubmission, Form
from app.services.suggestion_service import SuggestionService
from app.schemas.suggestion import (
    SuggestionsListResponse,
    SuggestionResponse,
    FieldStatisticsResponse
)
from app.core.auth import get_current_user
from app.core.logger import logger

router = APIRouter(
    prefix="/api/suggestions",
    tags=["suggestions"],
    responses={404: {"description": "Not found"}}
)


def resolve_effective_user_id(current_user: User, requested_user_id: int | None) -> int:
    """Resolve target user scope. Non-admin users can only access their own data."""
    if requested_user_id is None:
        return current_user.id

    if requested_user_id <= 0:
        raise HTTPException(status_code=400, detail="user_id phải là số dương")

    if not current_user.is_admin and requested_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Bạn không có quyền truy cập dữ liệu của người dùng khác")

    return requested_user_id


def _normalize_field_key(value: str) -> str:
    text = (value or "").strip().lower()
    text = text.replace("đ", "d")
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _field_similarity_score(target_key: str, candidate_key: str) -> float:
    if not target_key or not candidate_key:
        return 0.0
    if target_key == candidate_key:
        return 1.0
    if target_key in candidate_key or candidate_key in target_key:
        return 0.9

    target_tokens = set(target_key.split())
    candidate_tokens = set(candidate_key.split())
    if not target_tokens or not candidate_tokens:
        return 0.0

    overlap = len(target_tokens.intersection(candidate_tokens))
    if overlap == 0:
        return 0.0

    recall = overlap / max(1, len(target_tokens))
    precision = overlap / max(1, len(candidate_tokens))
    union = len(target_tokens.union(candidate_tokens))
    jaccard = overlap / max(1, union)
    return (0.55 * recall) + (0.35 * precision) + (0.10 * jaccard)


def _is_date_field_key(field_key: str) -> bool:
    key = f" {field_key or ''} "
    tokens = set((field_key or "").split())
    if " date " in key or " dob " in key or " birth " in key:
        return True
    if " ngay " in key and (" sinh " in key or " thang " in key or " nam " in key):
        return True
    return "ngaysinh" in key.replace(" ", "")


def _is_email_field_key(field_key: str) -> bool:
    key = f" {field_key or ''} "
    return " email " in key or " e mail " in key


def _is_phone_field_key(field_key: str) -> bool:
    key = f" {field_key or ''} "
    markers = (" sdt ", " so dien thoai ", " dien thoai ", " phone ", " mobile ", " tel ")
    return any(marker in key for marker in markers)


def _is_identifier_field_key(field_key: str) -> bool:
    key = f" {field_key or ''} "
    tokens = set((field_key or "").split())
    explicit_markers = (" mssv ", " cccd ", " cmnd ", " ma so ", " student id ", " employee id ", " user id ")
    if any(marker in key for marker in explicit_markers):
        return True
    if "id" in tokens or "code" in tokens:
        return True
    return "ma" in tokens and not _is_date_field_key(field_key)


def _infer_field_category(field_key: str, is_name_related: bool = False) -> str:
    if _is_date_field_key(field_key):
        return "date"
    if _is_email_field_key(field_key):
        return "email"
    if _is_phone_field_key(field_key):
        return "phone"
    if _is_identifier_field_key(field_key):
        return "identifier"
    if is_name_related:
        return "name"
    return "general"


def _is_date_like_value(value: str) -> bool:
    text = (value or "").strip()
    if not text:
        return False
    if re.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$", text):
        return True
    if re.match(r"^\d{1,2}[-/]\d{1,2}[-/]\d{4}$", text):
        return True
    return False


def _is_email_like_value(value: str) -> bool:
    text = (value or "").strip()
    return bool(re.match(r"^[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}$", text))


def _is_phone_like_value(value: str) -> bool:
    text = re.sub(r"\s+", "", (value or "").strip())
    if not text:
        return False
    if text.startswith("+"):
        text = text[1:]
    return bool(re.match(r"^\d{9,12}$", text))


def _is_identifier_like_value(value: str) -> bool:
    text = (value or "").strip()
    if not text:
        return False
    compact = re.sub(r"\s+", "", text)
    if re.match(r"^[A-Za-z]{0,4}\d{4,}$", compact):
        return True
    if re.match(r"^\d{5,}$", compact):
        return True
    return bool(re.match(r"^[A-Za-z0-9._-]{6,}$", compact))


def _value_matches_target_category(value: str, category: str, is_name_related: bool) -> bool:
    text = (value or "").strip()
    if not text:
        return False

    if category == "date":
        return _is_date_like_value(text)
    if category == "email":
        return _is_email_like_value(text)
    if category == "phone":
        return _is_phone_like_value(text)
    if category == "identifier":
        return _is_identifier_like_value(text)
    if category == "name" or is_name_related:
        return _is_name_like_value(text, min_tokens=1, max_tokens=6)

    # Generic text fields should not be polluted by long numeric IDs.
    if re.match(r"^\d{5,}$", text):
        return False
    return True


PERSONA_KEYWORDS: dict[str, tuple[str, ...]] = {
    "lecturer": (
        "giang vien",
        "giao vien",
        "ma giang vien",
        "ma giao vien",
        "bo mon",
        "hoc vi",
        "chuc danh",
        "gv",
    ),
    "student": (
        "sinh vien",
        "hoc sinh",
        "hoc vien",
        "ma sinh vien",
        "mssv",
        "sv",
        "lop",
        "nien khoa",
    ),
    "staff": (
        "nhan vien",
        "can bo",
        "chuyen vien",
        "ma nhan vien",
        "cbnv",
    ),
    "parent": (
        "phu huynh",
        "cha me",
        "nguoi giam ho",
    ),
}


def _extract_persona_tags_from_key(field_key: str) -> set[str]:
    key = f" {field_key or ''} "
    tags: set[str] = set()

    for persona, markers in PERSONA_KEYWORDS.items():
        if any(f" {marker} " in key for marker in markers):
            tags.add(persona)

    return tags


def _infer_form_persona_tags(field_names: list[str]) -> set[str]:
    tag_counts: dict[str, int] = defaultdict(int)

    for field_name in field_names:
        normalized = _normalize_field_key(field_name)
        for tag in _extract_persona_tags_from_key(normalized):
            tag_counts[tag] += 1

    if not tag_counts:
        return set()

    max_hits = max(tag_counts.values())
    return {tag for tag, count in tag_counts.items() if count == max_hits and count > 0}


def _is_persona_conflict(target_tags: set[str], source_tags: set[str]) -> bool:
    if not target_tags or not source_tags:
        return False
    return target_tags.isdisjoint(source_tags)


def _persona_match_score(target_tags: set[str], source_tags: set[str]) -> float:
    if not target_tags:
        return 0.0
    if not source_tags:
        return 0.2
    return 1.0 if not target_tags.isdisjoint(source_tags) else 0.0


def _target_is_identity_like(target_key: str) -> bool:
    tokens = set((target_key or "").split())
    return bool(tokens.intersection({"ho", "ten", "ma", "id", "name", "fullname", "nguoi"}))


def _value_conflicts_with_target_persona(value: str, target_tags: set[str], target_key: str) -> bool:
    if not target_tags or not _target_is_identity_like(target_key):
        return False

    normalized = _normalize_field_key(value)
    if not normalized:
        return False

    key = f" {normalized} "
    tokens = [tok for tok in normalized.split() if tok]

    if "lecturer" in target_tags:
        if " sinh vien " in key or " hoc sinh " in key or " mssv " in key or " sv " in key:
            return True
        if any(tok.startswith("sv") or tok.startswith("mssv") for tok in tokens):
            return True

    if "student" in target_tags:
        if " giang vien " in key or " giao vien " in key or " gv " in key:
            return True
        if any(tok.startswith("gv") for tok in tokens):
            return True

    return False


def _is_noise_suggestion_value(value: str) -> bool:
    text = (value or "").strip()
    if not text:
        return True

    # Filter technical/session-like strings that should not appear in name/address suggestions.
    if re.match(r"^[a-z0-9]+_[0-9]{8,}$", text.lower()):
        return True
    if re.match(r"^\d{10,}$", text):
        return True
    if re.match(r"^[._\-\s]{3,}$", text):
        return True
    if text.lower() in {"n/a", "na", "null", "none", "khong", "không", "test", "unknown", "...", "-"}:
        return True
    if len(text) > 80:
        return True

    return False


def _is_fullname_key(field_key: str) -> bool:
    key = f" {field_key} "
    formal_fullname_markers = (
        " toi ten la ",
        " ten toi la ",
        " ten day du ",
        " ten nguoi ",
        " nguoi khai ",
        " nguoi lam don ",
        " nguoi viet don ",
        " nguoi ky ten ",
    )
    return (
        " ho va ten " in key
        or " ho ten " in key
        or " fullname " in key
        or " full name " in key
        or any(marker in key for marker in formal_fullname_markers)
    )


def _is_given_name_key(field_key: str) -> bool:
    key = f" {field_key} "
    explicit_given_name_markers = (
        " ten goi ",
        " ten thuong goi ",
        " biet danh ",
        " nickname ",
        " first name ",
        " given name ",
        " ten rieng ",
    )
    if any(marker in key for marker in explicit_given_name_markers):
        return True

    # Avoid misclassifying formal labels like "Tôi tên là" as given-name only.
    if _is_fullname_key(field_key):
        return False

    return " ten " in key and " ho " not in key


def _is_family_name_key(field_key: str) -> bool:
    key = f" {field_key} "
    return " ho " in key and " ten " not in key


def _is_fullname_value(value: str) -> bool:
    parts = [p for p in (value or "").strip().split(" ") if p]
    return len(parts) >= 2


def _is_name_like_value(value: str, min_tokens: int, max_tokens: int) -> bool:
    text = (value or "").strip()
    if not text:
        return False

    normalized = _normalize_field_key(text)
    if not normalized:
        return False

    if re.search(r"\d", normalized):
        return False

    tokens = [tok for tok in normalized.split(" ") if tok]
    if not (min_tokens <= len(tokens) <= max_tokens):
        return False

    # Reject one-letter noise values such as "A" for person-name related fields.
    if any(len(tok) < 2 for tok in tokens):
        return False

    return True


def _name_quality_score(value: str) -> float:
    """Higher score means better person-name quality for ranking suggestions."""
    normalized = _normalize_field_key(value)
    if not normalized:
        return 0.0

    tokens = [tok for tok in normalized.split(" ") if tok]
    if not tokens:
        return 0.0

    score = float(len(tokens))
    score += sum(min(len(tok), 8) for tok in tokens) / 20.0
    score -= sum(1 for tok in tokens if len(tok) <= 2) * 0.7
    return score


@router.get("/cross-field")
async def get_cross_field_suggestions(
    user_id: int | None = Query(None, description="ID của user (optional, admin only)"),
    field_name: str = Query(..., description="Tên trường hiện tại"),
    current_form_id: int | None = Query(None, description="Form hiện tại để suy luận đối tượng điền"),
    current_template_id: int | None = Query(None, description="Template Word hiện tại để ưu tiên ngữ cảnh cùng mẫu"),
    q: str | None = Query(None, description="Tiền tố giá trị đang gõ để lọc gợi ý"),
    top_k: int = Query(5, description="Số lượng gợi ý tối đa (1-10)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get suggestions from similar fields across all user history (Word + Excel + other forms)."""
    try:
        effective_user_id = resolve_effective_user_id(current_user, user_id)

        if not field_name.strip():
            raise HTTPException(status_code=400, detail="field_name không được để trống")
        if top_k <= 0 or top_k > 10:
            raise HTTPException(status_code=400, detail="top_k phải từ 1 đến 10")

        target_key = _normalize_field_key(field_name)
        value_filter = _normalize_field_key(q or "")
        target_is_fullname = _is_fullname_key(target_key)
        target_is_given_name = _is_given_name_key(target_key)
        target_is_family_name = _is_family_name_key(target_key)
        target_is_name_related = target_is_fullname or target_is_given_name or target_is_family_name
        target_category = _infer_field_category(target_key, target_is_name_related)

        form_persona_tags: set[str] = set()
        if current_form_id and current_form_id > 0:
            form_fields = db.query(Field.field_name).filter(Field.form_id == current_form_id).all()
            form_persona_tags = _infer_form_persona_tags([row[0] for row in form_fields if row and row[0]])

        target_persona_tags = _extract_persona_tags_from_key(target_key)
        effective_target_persona = target_persona_tags or form_persona_tags

        rows = db.query(Entry.value, Entry.created_at, Field.field_name, Entry.user_id, Entry.form_id).join(
            Field, Entry.field_id == Field.id
        ).join(
            Form, Entry.form_id == Form.id
        ).filter(
            Entry.user_id == effective_user_id
        ).filter(
            or_(Form.form_type.is_(None), Form.form_type != "excel")
        ).filter(
            or_(Form.form_name.is_(None), Form.form_name != "Excel Smart Form")
        ).all()

        stats: dict[str, dict] = defaultdict(lambda: {
            "frequency": 0,
            "latest_time": None,
            "source_fields": set(),
            "similarity": 0.0,
            "own_hits": 0,
            "template_hits": 0,
            "persona_hits": 0,
            "persona_score": 0.0,
        })
        family_name_stats: dict[str, dict] = defaultdict(lambda: {"frequency": 0, "latest_time": None})
        given_name_stats: dict[str, dict] = defaultdict(lambda: {"frequency": 0, "latest_time": None})

        if current_template_id and current_template_id > 0:
            template_rows = db.query(WordSubmission.submission_data, WordSubmission.created_at).filter(
                WordSubmission.user_id == effective_user_id,
                WordSubmission.template_id == current_template_id
            ).all()

            for payload, created_at in template_rows:
                try:
                    submission_data = json.loads(payload or "{}")
                except Exception:
                    continue

                if not isinstance(submission_data, dict):
                    continue

                for submitted_key, submitted_value in submission_data.items():
                    submitted_key_text = str(submitted_key or "")
                    if submitted_key_text.startswith("__"):
                        continue

                    submitted_key_normalized = _normalize_field_key(submitted_key_text)
                    similarity = _field_similarity_score(target_key, submitted_key_normalized)
                    if similarity < 0.9:
                        continue

                    source_category = _infer_field_category(
                        submitted_key_normalized,
                        _is_fullname_key(submitted_key_normalized) or _is_given_name_key(submitted_key_normalized) or _is_family_name_key(submitted_key_normalized),
                    )
                    if target_category == "date" and source_category != "date":
                        continue
                    if target_category in {"email", "phone"} and source_category != target_category:
                        continue

                    value = (submitted_value or "") if isinstance(submitted_value, str) else str(submitted_value or "")
                    value = value.strip()
                    if not value or _is_noise_suggestion_value(value):
                        continue
                    if not _value_matches_target_category(value, target_category, target_is_name_related):
                        continue
                    if _value_conflicts_with_target_persona(value, effective_target_persona, target_key):
                        continue
                    if value_filter and not _normalize_field_key(value).startswith(value_filter):
                        continue

                    item = stats[value]
                    item["frequency"] += 1
                    item["template_hits"] += 1
                    item["source_fields"].add("Template hiện tại")
                    item["similarity"] = max(item["similarity"], 1.0)
                    item["persona_score"] = max(item["persona_score"], 1.0 if effective_target_persona else 0.0)
                    if created_at and (item["latest_time"] is None or created_at > item["latest_time"]):
                        item["latest_time"] = created_at

        for value, created_at, source_field_name, row_user_id, _row_form_id in rows:
            source_name = source_field_name or ""
            source_key = _normalize_field_key(source_name)
            similarity = _field_similarity_score(target_key, source_key)
            source_is_fullname = _is_fullname_key(source_key)
            source_is_given = _is_given_name_key(source_key)
            source_is_family = _is_family_name_key(source_key)
            source_is_name_related = source_is_fullname or source_is_given or source_is_family
            source_category = _infer_field_category(source_key, source_is_name_related)
            source_persona_tags = _extract_persona_tags_from_key(source_key)

            if _is_persona_conflict(effective_target_persona, source_persona_tags):
                continue

            # When target persona is known, suppress unlabeled generic fields to avoid role mixing.
            if effective_target_persona and not source_persona_tags and similarity < 0.85:
                continue

            # Name-specific boosting so Word/Excel can share Họ + Tên with Họ và tên.
            if target_is_fullname and source_is_fullname:
                similarity = max(similarity, 0.97)
            elif target_is_fullname and (source_is_given or source_is_family):
                similarity = max(similarity, 0.92)
            elif (target_is_given_name or target_is_family_name) and source_is_fullname:
                similarity = max(similarity, 0.9)

            min_similarity = 0.55
            if target_is_name_related:
                min_similarity = 0.38
            if target_category in {"date", "email", "phone", "identifier"}:
                min_similarity = max(min_similarity, 0.62)

            if target_category == "date" and source_category != "date" and similarity < 0.9:
                continue
            if target_category in {"email", "phone"} and source_category != target_category and similarity < 0.9:
                continue
            if target_category == "identifier" and source_category in {"date", "name"} and similarity < 0.9:
                continue
            if target_category == "name" and source_category == "identifier" and similarity < 0.9:
                continue

            # only keep reasonably similar fields to avoid noisy suggestions
            if similarity < min_similarity:
                continue

            suggestion_value = (value or "").strip()
            if not suggestion_value:
                continue
            if _is_noise_suggestion_value(suggestion_value):
                continue
            if not _value_matches_target_category(suggestion_value, target_category, target_is_name_related):
                continue
            if _value_conflicts_with_target_persona(suggestion_value, effective_target_persona, target_key):
                continue

            # For fullname target, collect Ho/Ten separately to build a combined suggestion later.
            # Restrict synthesis to current user to avoid mixing identities across users.
            if row_user_id == effective_user_id and target_is_fullname and (source_is_given or source_is_family):
                if source_is_family:
                    fam = family_name_stats[suggestion_value]
                    fam["frequency"] += 1
                    if created_at and (fam["latest_time"] is None or created_at > fam["latest_time"]):
                        fam["latest_time"] = created_at
                if source_is_given:
                    giv = given_name_stats[suggestion_value]
                    giv["frequency"] += 1
                    if created_at and (giv["latest_time"] is None or created_at > giv["latest_time"]):
                        giv["latest_time"] = created_at
                continue

            # If asking for given/family name and historical value is full name, split to best guess.
            if (target_is_given_name or target_is_family_name) and " " in suggestion_value:
                parts = [p for p in suggestion_value.split(" ") if p]
                if len(parts) >= 2:
                    suggestion_value = parts[-1] if target_is_given_name else " ".join(parts[:-1])

            if target_is_given_name and not _is_name_like_value(suggestion_value, min_tokens=1, max_tokens=2):
                continue

            if target_is_family_name and not _is_name_like_value(suggestion_value, min_tokens=1, max_tokens=3):
                continue

            # For fullname target, ignore fragmented one-part values.
            if target_is_fullname and not _is_fullname_value(suggestion_value):
                continue

            if target_is_fullname and not _is_name_like_value(suggestion_value, min_tokens=2, max_tokens=6):
                continue

            if target_is_name_related and _name_quality_score(suggestion_value) <= 0.8:
                continue

            if value_filter and not _normalize_field_key(suggestion_value).startswith(value_filter):
                continue

            item = stats[suggestion_value]
            item["frequency"] += 1
            item["source_fields"].add(source_name)
            item["similarity"] = max(item["similarity"], similarity)
            persona_score = _persona_match_score(effective_target_persona, source_persona_tags)
            item["persona_score"] = max(item["persona_score"], persona_score)
            if persona_score >= 1.0:
                item["persona_hits"] += 1
            if row_user_id == effective_user_id:
                item["own_hits"] += 1
            if created_at and (item["latest_time"] is None or created_at > item["latest_time"]):
                item["latest_time"] = created_at

        # Compose fullname suggestions from Ho + Ten history if target requires full name.
        if target_is_fullname and family_name_stats and given_name_stats:
            top_family = sorted(
                family_name_stats.items(),
                key=lambda x: (x[1]["frequency"], x[1]["latest_time"] or 0),
                reverse=True
            )[:5]
            top_given = sorted(
                given_name_stats.items(),
                key=lambda x: (x[1]["frequency"], x[1]["latest_time"] or 0),
                reverse=True
            )[:5]

            for family_value, fam_stat in top_family:
                for given_value, giv_stat in top_given:
                    full_name = f"{family_value} {given_value}".strip()
                    if not _is_fullname_value(full_name):
                        continue
                    if value_filter and not _normalize_field_key(full_name).startswith(value_filter):
                        continue

                    item = stats[full_name]
                    # Synthetic frequency score from paired name parts.
                    item["frequency"] = max(item["frequency"], fam_stat["frequency"] + giv_stat["frequency"])
                    item["source_fields"].add("Họ + Tên")
                    item["similarity"] = max(item["similarity"], 0.95)

                    latest_candidates = [
                        t for t in [fam_stat.get("latest_time"), giv_stat.get("latest_time"), item.get("latest_time")]
                        if t is not None
                    ]
                    if latest_candidates:
                        item["latest_time"] = max(latest_candidates)

        if not stats:
            return {
                "field_name": field_name,
                "suggestions": [],
                "total_count": 0,
                "message": "Chưa có lịch sử ở các trường tương tự"
            }

        sorted_items = sorted(
            stats.items(),
            key=lambda x: (
                x[1]["template_hits"],
                x[1]["persona_hits"],
                x[1]["persona_score"],
                x[1]["own_hits"],
                _name_quality_score(x[0]) if target_is_name_related else 0.0,
                x[1]["similarity"],
                x[1]["frequency"],
                x[1]["latest_time"] or 0,
            ),
            reverse=True
        )[:top_k]

        suggestions = [
            {
                "value": value,
                "frequency": detail["frequency"],
                "latest_time": detail["latest_time"].isoformat() if detail["latest_time"] else None,
                "source_fields": sorted([f for f in detail["source_fields"] if f]),
                "similarity": round(float(detail["similarity"]), 3),
            }
            for value, detail in sorted_items
        ]

        return {
            "field_name": field_name,
            "target_persona": sorted(list(effective_target_persona)),
            "suggestions": suggestions,
            "total_count": len(suggestions),
            "message": "Suggestions từ lịch sử trường tương tự"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in get_cross_field_suggestions: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Lỗi khi lấy gợi ý liên thông")


@router.get("")
async def get_suggestions(
    user_id: int | None = Query(None, description="ID của user (optional, admin only)"),
    field_id: int = Query(..., description="ID của field"),
    top_k: int = Query(5, description="Số lượng gợi ý top (mặc định 5, max 5)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    API GET /api/suggestions
    
    Logic:
    - Lần 1 (< 2 entries): Trả empty list, không gợi ý
    - Lần 2+ (>= 2 entries): Gợi ý từ database
    
    Input:
    - user_id: ID của user
    - field_id: ID của field
    - top_k: Số lượng gợi ý top
    
    Output:
    - Danh sách gợi ý (rỗng nếu chưa đủ entries)
    - entry_count: Số entries đã lưu
    - is_first_entry: true nếu đây là lần nhập đầu tiên
    """
    try:
        effective_user_id = resolve_effective_user_id(current_user, user_id)
        logger.info(f"Request suggestions: user_id={effective_user_id}, field_id={field_id}, top_k={top_k}")
        
        # Validate input
        if field_id <= 0:
            raise HTTPException(
                status_code=400,
                detail="field_id phải là số dương"
            )
        
        if top_k <= 0 or top_k > 5:
            raise HTTPException(
                status_code=400,
                detail="top_k phải từ 1 đến 5"
            )
        
        # Lấy TẤT CẢ entries của field này từ lịch sử
        entries = db.query(Entry).filter(
            Entry.user_id == effective_user_id,
            Entry.field_id == field_id
        ).all()
        
        entry_count = len(entries)
        logger.info(f"Field {field_id} has {entry_count} entries")
        
        # Logic: Nếu không có entries, không gợi ý
        # Show suggestions from >= 1 entry (show history)
        if entry_count < 1:
            logger.info(f"No entries found ({entry_count} < 1), no suggestions")
            return {
                "user_id": effective_user_id,
                "field_id": field_id,
                "suggestions": [],
                "total_count": 0,
                "entry_count": entry_count,
                "is_first_entry": True,
                "message": "Nhập dữ liệu lần đầu để có gợi ý"
            }
        
        # Có entries: Lấy danh sách giá trị duy nhất + lần submit gần nhất
        from collections import OrderedDict
        from datetime import datetime
        
        # Tính tần suất và lấy entry gần nhất cho mỗi giá trị
        value_stats = {}
        for entry in entries:
            value = entry.value
            if value not in value_stats:
                value_stats[value] = {
                    'frequency': 0,
                    'latest_time': entry.created_at or datetime.utcnow()
                }
            value_stats[value]['frequency'] += 1
            # Cập nhật thời gian gần nhất
            if entry.created_at:
                if entry.created_at > value_stats[value]['latest_time']:
                    value_stats[value]['latest_time'] = entry.created_at
        
        # Sắp xếp: 1. Tần suất (giảm dần), 2. Thời gian gần nhất (giảm dần)
        sorted_suggestions = sorted(
            value_stats.items(),
            key=lambda x: (x[1]['frequency'], x[1]['latest_time']),
            reverse=True
        )[:top_k]
        
        suggestions = [
            {
                "value": value,
                "frequency": stats['frequency'],
                "latest_time": stats['latest_time'].isoformat() if stats['latest_time'] else None
            }
            for value, stats in sorted_suggestions
        ]
        
        logger.info(f"Generated {len(suggestions)} suggestions from {entry_count} entries")
        
        return {
            "user_id": effective_user_id,
            "field_id": field_id,
            "suggestions": suggestions,
            "total_count": len(suggestions),
            "entry_count": entry_count,
            "is_first_entry": False,
            "message": "Suggestions retrieved from history"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in get_suggestions: {str(e)}", exc_info=True)
        return {
            "user_id": current_user.id,
            "field_id": field_id,
            "suggestions": [],
            "total_count": 0,
            "entry_count": 0,
            "is_first_entry": True,
            "message": f"Error: {str(e)}"
        }


@router.get(
    "/debug",
    summary="Debug suggestions endpoint",
    description="Test endpoint"
)
async def debug_suggestions(
    user_id: int = Query(1),
    field_index: int = Query(0),
    form_id: int = Query(1),
    db: Session = Depends(get_db)
):
    try:
        logger.info(f"[DEBUG] Starting: user_id={user_id}, field_index={field_index}, form_id={form_id}")
        
        # Step 1: Get fields
        fields = db.query(Field).filter(Field.form_id == form_id).all()
        logger.info(f"[DEBUG] Found {len(fields)} fields")
        
        if not fields or field_index >= len(fields):
            return {"error": f"Not enough fields. Total: {len(fields) if fields else 0}, Index: {field_index}"}
        
        field = fields[field_index]
        logger.info(f"[DEBUG] Field: id={field.id}, name={field.field_name}")
        
        # Just return field info, don't call service
        return {
            "success": True,
            "field": {"id": field.id, "name": field.field_name},
            "suggestions": [],
            "count": 0
        }
    except Exception as e:
        logger.error(f"[DEBUG] Error: {str(e)}", exc_info=True)
        return {"error": str(e)}


# Endpoint /by-index disabled - use /suggestions?user_id=X&field_id=Y instead

@router.get(
    "/by-name",
    response_model=SuggestionsListResponse,
    summary="Get suggestions for a field by name",
    description="Lấy gợi ý theo tên field (dùng cho Word forms)"
)
async def get_suggestions_by_name(
    user_id: int | None = Query(None, description="ID của user (optional, admin only)"),
    field_name: str = Query(..., description="Tên field (e.g. họ_và_tên)"),
    form_id: int = Query(1, description="ID của form (mặc định 1)"),
    top_k: int = Query(5, description="Số lượng gợi ý top (mặc định 5, max 5)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    API GET /api/suggestions/by-name
    
    Lấy gợi ý dựa trên tên field thay vì field_id
    Tự động tìm field_id từ field_name và form_id
    
    Logic:
    - Lần đầu (< 2 entries): Trả empty list, không gợi ý
    - Lần 2+ (>= 2 entries): Gợi ý từ database
    
    Input:
    - user_id: ID của user
    - field_name: Tên field (e.g. họ_và_tên)
    - form_id: ID của form (mặc định 1)
    - top_k: Số lượng gợi ý top
    
    Output:
    - Danh sách gợi ý (rỗng nếu chưa đủ entries)
    - entry_count: Số entries đã lưu
    - is_first_entry: true nếu đây là lần nhập đầu tiên
    """
    try:
        effective_user_id = resolve_effective_user_id(current_user, user_id)
        logger.info(f"Request suggestions by name: user_id={effective_user_id}, field_name={field_name}, top_k={top_k}")
        
        # Validate input
        if not field_name:
            raise HTTPException(status_code=400, detail="field_name không được để trống")
        
        if top_k <= 0 or top_k > 5:
            raise HTTPException(status_code=400, detail="top_k phải từ 1 đến 5")
        
        # Tìm field theo field_name
        field = db.query(Field).filter(
            Field.form_id == form_id,
            Field.field_name == field_name
        ).first()
        
        if not field:
            logger.warning(f"Field not found: form_id={form_id}, field_name={field_name}")
            # Trả empty list nếu field không tồn tại
            return SuggestionsListResponse(
                user_id=effective_user_id,
                field_id=0,
                suggestions=[],
                total_count=0,
                entry_count=0,
                is_first_entry=True,
                message="Field not found"
            )
        
        # Kiểm tra số entries hiện tại
        entry_count = db.query(Entry).filter(
            Entry.user_id == effective_user_id,
            Entry.field_id == field.id
        ).count()
        
        logger.info(f"Field {field_name} has {entry_count} entries")
        
        # Logic: Show suggestions from >= 1 entry
        # No minimum threshold - show history right away
        if entry_count < 1:
            logger.info(f"Not enough entries ({entry_count} < 1), no suggestions")
            return SuggestionsListResponse(
                user_id=effective_user_id,
                field_id=field.id,
                suggestions=[],
                total_count=0,
                entry_count=entry_count,
                is_first_entry=True,
                message="Nhập dữ liệu lần đầu để có gợi ý"
            )
        
        # Lần 2+: Gợi ý từ cache table
        normalized_field_key = _normalize_field_key(field.field_name)
        target_is_fullname = _is_fullname_key(normalized_field_key)
        target_is_given_name = _is_given_name_key(normalized_field_key)
        target_is_family_name = _is_family_name_key(normalized_field_key)
        target_is_name_related = target_is_fullname or target_is_given_name or target_is_family_name

        suggestions_from_cache = db.query(Suggestion).filter(
            Suggestion.user_id == effective_user_id,
            Suggestion.field_id == field.id
        ).order_by(Suggestion.ranking.desc()).limit(100).all()

        filtered_cache = []
        for sug in suggestions_from_cache:
            value = (sug.suggested_value or "").strip()
            if not value:
                continue

            if target_is_fullname and not _is_name_like_value(value, min_tokens=2, max_tokens=6):
                continue
            if target_is_given_name and not _is_name_like_value(value, min_tokens=1, max_tokens=2):
                continue
            if target_is_family_name and not _is_name_like_value(value, min_tokens=1, max_tokens=3):
                continue
            if target_is_name_related and _name_quality_score(value) <= 0.8:
                continue

            filtered_cache.append(sug)

        filtered_cache.sort(
            key=lambda s: (
                _name_quality_score(s.suggested_value) if target_is_name_related else 0.0,
                s.ranking,
                s.frequency,
            ),
            reverse=True,
        )

        filtered_cache = filtered_cache[:(top_k or 5)]
        
        suggestions = [
            {"value": sug.suggested_value, "frequency": sug.frequency, "ranking": sug.ranking}
            for sug in filtered_cache
        ]
        
        logger.info(f"Successfully retrieved {len(suggestions)} suggestions for field {field_name}")
        
        return SuggestionsListResponse(
            user_id=effective_user_id,
            field_id=field.id,
            suggestions=suggestions,
            total_count=len(suggestions),
            entry_count=entry_count,
            is_first_entry=False,
            message="Suggestions retrieved from history"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in get_suggestions_by_name: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Lỗi khi lấy gợi ý")



@router.get(
    "/history",
    response_model=SuggestionsListResponse,
    summary="Get suggestions based on history",
    description="Lấy gợi ý dựa vào lịch sử trong khoảng thời gian"
)
async def get_suggestions_with_history(
    user_id: int | None = Query(None, description="ID của user (optional, admin only)"),
    field_id: int = Query(..., description="ID của field"),
    days: int = Query(30, description="Số ngày quay lại (mặc định 30)"),
    top_k: int = Query(3, description="Số lượng gợi ý top (mặc định 3)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    API GET /api/suggestions/history
    
    Lấy gợi ý dựa vào lịch sử trong khoảng thời gian cụ thể
    """
    try:
        effective_user_id = resolve_effective_user_id(current_user, user_id)
        logger.info(f"Request suggestions with history: user_id={effective_user_id}, field_id={field_id}, days={days}, top_k={top_k}")
        
        # Validate input
        if field_id <= 0:
            raise HTTPException(
                status_code=400,
                detail="field_id phải là số dương"
            )
        
        if days <= 0 or days > 365:
            raise HTTPException(
                status_code=400,
                detail="days phải từ 1 đến 365"
            )
        
        if top_k <= 0 or top_k > 10:
            raise HTTPException(
                status_code=400,
                detail="top_k phải từ 1 đến 10"
            )
        
        # Gọi service để lấy gợi ý
        suggestions = SuggestionService.get_suggestions_with_history(
            db=db,
            user_id=effective_user_id,
            field_id=field_id,
            days=days,
            top_k=top_k
        )
        
        logger.info(f"Successfully retrieved {len(suggestions)} suggestions from history")
        
        return SuggestionsListResponse(
            user_id=effective_user_id,
            field_id=field_id,
            suggestions=suggestions,
            total_count=len(suggestions),
            message=f"Suggestions retrieved from {days} days of history"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in get_suggestions_with_history: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Lỗi khi lấy gợi ý từ lịch sử"
        )


@router.get(
    "/stats",
    response_model=FieldStatisticsResponse,
    summary="Get field statistics",
    description="Lấy thống kê về entries của một field"
)
async def get_field_stats(
    user_id: int | None = Query(None, description="ID của user (optional, admin only)"),
    field_id: int = Query(..., description="ID của field"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    API GET /api/suggestions/stats
    
    Lấy thống kê về entries của một field
    """
    try:
        effective_user_id = resolve_effective_user_id(current_user, user_id)
        logger.info(f"Request stats: user_id={effective_user_id}, field_id={field_id}")
        
        # Validate input
        if field_id <= 0:
            raise HTTPException(
                status_code=400,
                detail="field_id phải là số dương"
            )
        
        # Gọi service để lấy thống kê
        stats = SuggestionService.get_field_statistics(
            db=db,
            user_id=effective_user_id,
            field_id=field_id
        )
        
        logger.info(f"Successfully retrieved stats: {stats}")
        
        return FieldStatisticsResponse(**stats)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in get_field_stats: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Lỗi khi lấy thống kê"
        )

@router.post(
    "/save",
    summary="Save entry from plugin",
    description="Lưu entry từ Word plugin vào database"
)
async def save_entry(
    user_id: int | None = Query(None, description="ID của user (optional, admin only)"),
    field_id: int = Query(..., description="ID của field"),
    form_id: int = Query(..., description="ID của form"),
    value: str = Query(..., description="Giá trị được nhập"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    API POST /api/suggestions/save
    
    Lưu entry từ Word plugin.
    Plugin sẽ gọi endpoint này mỗi khi user nhập xong một field.
    
    Input:
    - user_id: ID của user
    - field_id: ID của field
    - form_id: ID của form
    - value: Giá trị được nhập
    
    Output:
    - status: "success" hoặc error
    - entry_id: ID của entry vừa được lưu
    """
    try:
        effective_user_id = resolve_effective_user_id(current_user, user_id)
        logger.info(f"Saving entry: user_id={effective_user_id}, field_id={field_id}, value={value}")
        
        # Validate input
        if field_id <= 0 or form_id <= 0:
            raise HTTPException(
                status_code=400,
                detail="field_id, form_id phải là số dương"
            )
        
        if not value or len(value.strip()) == 0:
            raise HTTPException(
                status_code=400,
                detail="value không được để trống"
            )
        
        # Gọi service để lưu entry
        result = SuggestionService.save_entry(
            db=db,
            user_id=effective_user_id,
            field_id=field_id,
            form_id=form_id,
            value=value.strip()
        )
        
        logger.info(f"Entry saved successfully: {result}")
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in save_entry: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Lỗi khi lưu entry: {str(e)}"
        )