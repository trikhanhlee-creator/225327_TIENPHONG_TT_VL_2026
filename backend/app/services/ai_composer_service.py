"""
Service để tích hợp AI suggestions cho soạn thảo tài liệu
- Kết nối OpenAI/Gemini API
- Xử lý suggestions
- Lưu lịch sử soạn thảo
"""

from typing import List, Dict, Optional, Any
from app.core.config import settings
from app.core.logger import logger
from sqlalchemy.orm import Session
from app.db.models import Document, CompositionHistory, User
from app.db.session import SessionLocal
from datetime import datetime
import json
import asyncio
import re


class AIComposerService:
    """Service cho AI document composition"""

    def __init__(self):
        """Khởi tạo AI service"""
        requested_profile = (getattr(settings, "AI_PROFILE", "") or settings.AI_PROVIDER or "auto").strip().lower()
        self.profile = requested_profile if requested_profile in {"auto", "openrouter", "openai", "gemini"} else "auto"
        self.failover_enabled = bool(getattr(settings, "AI_FAILOVER_ENABLED", True))
        self.api_provider = settings.AI_PROVIDER  # runtime active provider
        self.api_key = settings.AI_API_KEY

        self.openrouter_client = None
        self.openai_client = None
        self.gemini_client = None
        self.client = None
        self._init_ai_client()

    def _get_active_model(self) -> str:
        return self._get_active_model_for_provider(self.api_provider)

    def _get_active_model_for_provider(self, provider: str) -> str:
        provider_name = (provider or "").strip().lower()
        if provider_name == "openrouter":
            return settings.OPENROUTER_MODEL
        if provider_name == "openai":
            return settings.OPENAI_MODEL or settings.AI_MODEL or "gpt-4o-mini"
        if provider_name == "gemini":
            return "gemini-pro"
        return settings.AI_MODEL or "gpt-4o-mini"

    def _parse_model_csv(self, raw: Optional[str]) -> List[str]:
        if not raw:
            return []
        parts = [item.strip() for item in str(raw).split(",")]
        return [item for item in parts if item]

    def _get_openai_candidate_models(self) -> List[str]:
        candidates: List[str] = []

        primary = (settings.OPENAI_MODEL or settings.AI_MODEL or "gpt-4o-mini").strip()
        if primary:
            candidates.append(primary)

        candidates.extend(self._parse_model_csv(getattr(settings, "OPENAI_FALLBACK_MODELS", "")))
        candidates.extend(["gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"])

        unique: List[str] = []
        seen = set()
        for model_name in candidates:
            normalized = str(model_name or "").strip()
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(normalized)

        return unique

    def _resolve_openrouter_key(self) -> str:
        explicit = (settings.OPENROUTER_API_KEY or "").strip()
        if explicit:
            return explicit
        shared = (settings.AI_API_KEY or "").strip()
        if shared.startswith("sk-or-"):
            return shared
        return ""

    def _resolve_openai_key(self) -> str:
        explicit = (getattr(settings, "OPENAI_API_KEY", "") or "").strip()
        if explicit:
            return explicit
        shared = (settings.AI_API_KEY or "").strip()
        if shared and not shared.startswith("sk-or-"):
            return shared
        return ""

    def _get_provider_attempt_order(self) -> List[str]:
        if self.profile == "openrouter":
            preferred = ["openrouter", "openai"]
        elif self.profile == "openai":
            preferred = ["openai", "openrouter"]
        elif self.profile == "gemini":
            preferred = ["gemini"]
        else:
            # auto: ưu tiên openrouter (model free) rồi đến openai.
            preferred = ["openrouter", "openai", "gemini"]

        if not self.failover_enabled:
            preferred = preferred[:1]

        available = []
        for provider in preferred:
            if provider == "openrouter" and self.openrouter_client:
                available.append(provider)
            elif provider == "openai" and self.openai_client:
                available.append(provider)
            elif provider == "gemini" and self.gemini_client:
                available.append(provider)
        return available

    def _get_openrouter_candidate_models(self) -> List[str]:
        """Trả về danh sách model OpenRouter để thử lần lượt khi gọi composer."""
        candidates: List[str] = []

        primary = (settings.OPENROUTER_MODEL or "").strip()
        if primary:
            candidates.append(primary)

        configured_fallbacks = self._parse_model_csv(getattr(settings, "OPENROUTER_FALLBACK_MODELS", ""))
        candidates.extend(configured_fallbacks)

        # Reuse shared AI provider model registry if available in this repository.
        try:
            from app.api.providers.config import AIConfig  # Lazy import to avoid tight coupling on startup
            candidates.extend(getattr(AIConfig, "AVAILABLE_MODELS", []))
        except Exception:
            pass

        # Add safe defaults to increase chance of success on free/public OpenRouter tiers.
        candidates.extend([
            "openai/gpt-oss-120b:free",
            "meta-llama/llama-3.3-70b-instruct:free",
            "google/gemma-4-31b-it:free",
            "qwen/qwen3-next-80b-a3b-instruct:free",
            "minimax/minimax-m2.5:free",
            "nvidia/nemotron-3-super-120b-a12b:free",
            "openai/gpt-oss-20b:free",
        ])

        unique: List[str] = []
        seen = set()
        for model_name in candidates:
            normalized = str(model_name or "").strip()
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(normalized)

        return unique

    def _is_auth_error(self, error: Exception) -> bool:
        text = str(error).lower()
        return any(marker in text for marker in [
            "401",
            "user not found",
            "invalid api key",
            "unauthorized",
            "authentication",
        ])

    def _is_quota_or_rate_error(self, error: Exception) -> bool:
        text = str(error).lower()
        return any(marker in text for marker in [
            "429",
            "insufficient_quota",
            "quota",
            "rate limit",
            "too many requests",
            "billing",
        ])

    def _init_ai_client(self):
        """Khởi tạo các AI client theo profile để có thể failover linh hoạt."""
        self.client = None
        self.openrouter_client = None
        self.openai_client = None
        self.gemini_client = None

        try:
            try:
                from openai import AsyncOpenAI

                openrouter_key = self._resolve_openrouter_key()
                if openrouter_key:
                    self.openrouter_client = AsyncOpenAI(
                        api_key=openrouter_key,
                        base_url=settings.OPENROUTER_BASE_URL,
                        default_headers={
                            "HTTP-Referer": settings.OPENROUTER_SITE_URL,
                            "X-Title": settings.OPENROUTER_APP_NAME,
                        }
                    )
                    logger.info(f"OpenRouter client initialized with model: {settings.OPENROUTER_MODEL}")
                else:
                    logger.warning("OpenRouter API key is missing")

                openai_key = self._resolve_openai_key()
                if openai_key:
                    self.openai_client = AsyncOpenAI(api_key=openai_key)
                    logger.info(f"OpenAI client initialized with model: {settings.OPENAI_MODEL or settings.AI_MODEL or 'gpt-4o-mini'}")
                else:
                    logger.warning("OpenAI API key is missing")
            except ImportError:
                logger.warning("OpenAI package not installed, OpenAI/OpenRouter clients unavailable")

            # Keep Gemini support as optional third profile.
            if self.profile == "gemini" or settings.AI_PROVIDER == "gemini":
                try:
                    import google.generativeai as genai
                    gemini_key = (settings.AI_API_KEY or "").strip()
                    if gemini_key:
                        genai.configure(api_key=gemini_key)
                        self.gemini_client = genai.GenerativeModel('gemini-pro')
                        logger.info("Gemini client initialized")
                    else:
                        logger.warning("Gemini API key is missing")
                except ImportError:
                    logger.warning("Google Generative AI package not installed")

            provider_order = self._get_provider_attempt_order()
            if provider_order:
                primary = provider_order[0]
                self.api_provider = primary
                if primary == "openrouter":
                    self.client = self.openrouter_client
                elif primary == "openai":
                    self.client = self.openai_client
                elif primary == "gemini":
                    self.client = self.gemini_client
                logger.info(f"Composer AI profile={self.profile}, failover={self.failover_enabled}, active provider={primary}, order={provider_order}")
            else:
                logger.warning("No AI provider client available, composer will use local fallback suggestions")

        except Exception as e:
            logger.error(f"Error initializing AI client: {e}")
            self.client = None

    async def get_text_suggestions(
        self,
        context: str,
        max_suggestions: int = 3,
        suggestion_length: int = 10,
        mode: str = "continuation",
        original_text: Optional[str] = None,
        instruction: Optional[str] = None,
        rewrite_scope: Optional[str] = None
    ) -> List[Dict]:
        """
        Lấy AI suggestions cho tiếp theo của text

        Args:
            context: Văn bản hiện tại (ngữ cảnh)
            max_suggestions: Số lượng suggestions (mặc định 3)
            suggestion_length: Độ dài trung bình của mỗi suggestion (từ)
            mode: continuation (viết tiếp) | rewrite (viết lại giữ ý)
            original_text: Câu/đoạn gốc cần viết lại (dùng cho mode rewrite)
            instruction: Yêu cầu chỉnh sửa do người dùng nhập
            rewrite_scope: phrase | sentence | document (tùy chọn, chỉ dùng cho mode rewrite)

        Returns:
            Danh sách dict chứa suggestions
            Mỗi item: {'text': 'gợi ý', 'confidence': 0.8}
        """
        try:
            provider_order = self._get_provider_attempt_order()
            if not provider_order:
                logger.warning("No active AI provider client, returning mock suggestions")
                return self._get_mock_suggestions(
                    context=context,
                    max_suggestions=max_suggestions,
                    mode=mode,
                    original_text=original_text,
                    instruction=instruction,
                    rewrite_scope=rewrite_scope
                )

            logger.info(f"Getting suggestions for context: {context[:50]}... provider order={provider_order}")

            last_error: Optional[Exception] = None
            for provider in provider_order:
                try:
                    if provider == "openrouter":
                        suggestions = await self._get_openai_suggestions(
                            context,
                            max_suggestions,
                            suggestion_length,
                            mode=mode,
                            original_text=original_text,
                            instruction=instruction,
                            rewrite_scope=rewrite_scope,
                            provider_override="openrouter",
                            client_override=self.openrouter_client,
                            strict_errors=True,
                        )
                    elif provider == "openai":
                        suggestions = await self._get_openai_suggestions(
                            context,
                            max_suggestions,
                            suggestion_length,
                            mode=mode,
                            original_text=original_text,
                            instruction=instruction,
                            rewrite_scope=rewrite_scope,
                            provider_override="openai",
                            client_override=self.openai_client,
                            strict_errors=True,
                        )
                    elif provider == "gemini":
                        original_provider = self.api_provider
                        original_client = self.client
                        self.api_provider = "gemini"
                        self.client = self.gemini_client
                        try:
                            suggestions = await self._get_gemini_suggestions(
                                context,
                                max_suggestions,
                                suggestion_length,
                                mode=mode,
                                original_text=original_text,
                                instruction=instruction,
                                rewrite_scope=rewrite_scope
                            )
                        finally:
                            self.api_provider = original_provider
                            self.client = original_client
                    else:
                        continue

                    if suggestions:
                        logger.info(f"Got {len(suggestions)} suggestions via provider={provider}")
                        return suggestions
                except Exception as provider_error:
                    last_error = provider_error
                    logger.warning(f"Provider failed [{provider}]: {provider_error}")
                    continue

            if last_error:
                logger.error(f"All AI providers failed, using mock suggestions: {last_error}")

            return self._get_mock_suggestions(
                context=context,
                max_suggestions=max_suggestions,
                mode=mode,
                original_text=original_text,
                instruction=instruction,
                rewrite_scope=rewrite_scope
            )

        except Exception as e:
            logger.error(f"Error getting suggestions: {e}")
            return self._get_mock_suggestions(
                context=context,
                max_suggestions=max_suggestions,
                mode=mode,
                original_text=original_text,
                instruction=instruction,
                rewrite_scope=rewrite_scope
            )

    def _extract_last_sentence(self, context: str) -> str:
        text = (context or "").strip()
        if not text:
            return ""
        parts = re.split(r'(?<=[.!?…])\s+', text)
        return (parts[-1] or text).strip()

    def _resolve_rewrite_scope(self, original_text: Optional[str], rewrite_scope: Optional[str] = None) -> str:
        requested_scope = (rewrite_scope or "").strip().lower()
        if requested_scope in {"phrase", "sentence", "document"}:
            return requested_scope

        source = (original_text or "").strip()
        if not source:
            return "sentence"

        normalized = re.sub(r'\s+', ' ', source)
        word_count = len(re.findall(r'\w+', normalized))
        has_line_break = "\n" in source
        has_terminal_punctuation = bool(re.search(r'[.!?…]["\')\]]*$', normalized))

        if has_line_break and word_count >= 40:
            return "document"

        if has_line_break or has_terminal_punctuation or word_count >= 12:
            return "sentence"
        return "phrase"

    def _resolve_style_hint(self, instruction: Optional[str]) -> str:
        """Suy ra phong cách mong muốn từ yêu cầu người dùng."""
        text = (instruction or "").strip().lower()
        if not text:
            return "Tự nhiên, rõ ràng, bám sát ngữ cảnh."

        if any(key in text for key in ["trang trọng", "lịch sự", "formal"]):
            return "Trang trọng, chuyên nghiệp, lịch sự."
        if any(key in text for key in ["thân thiện", "gần gũi", "ấm áp"]):
            return "Thân thiện, gần gũi, dễ tiếp nhận."
        if any(key in text for key in ["ngắn", "súc tích", "rút gọn", "ngắn gọn"]):
            return "Ngắn gọn, súc tích, đi thẳng ý chính."
        if any(key in text for key in ["thuyết phục", "mạnh mẽ", "ấn tượng"]):
            return "Thuyết phục, có điểm nhấn, rõ giá trị."
        return "Tự nhiên, rõ ràng, bám sát yêu cầu người dùng."

    def _build_rewrite_prompt(
        self,
        context: str,
        max_suggestions: int,
        suggestion_length: int,
        original_text: Optional[str],
        instruction: Optional[str],
        rewrite_scope: Optional[str] = None,
    ) -> str:
        source_text = (original_text or context or "").strip()
        user_instruction = (instruction or "").strip()
        style_hint = self._resolve_style_hint(user_instruction)
        resolved_scope = self._resolve_rewrite_scope(source_text, rewrite_scope)
        source_word_count = len(re.findall(r'\w+', source_text))

        if resolved_scope == "document":
            min_words = max(24, int(source_word_count * 0.6) if source_word_count else max(30, suggestion_length))
            max_words = max(min_words + 18, int(source_word_count * 1.45) if source_word_count else max(56, suggestion_length + 20))

            return f"""Bạn là trợ lý viết tiếng Việt chuyên CHỈNH SỬA TOÀN BÀI VIẾT.
Nhiệm vụ: đề xuất {max_suggestions} phiên bản viết lại cho toàn bộ bài gốc theo yêu cầu người dùng, vẫn GIỮ NGUYÊN ý chính.

Bài gốc cần chỉnh sửa toàn bộ:
{source_text}

Yêu cầu chỉnh sửa của người dùng (ưu tiên cao):
{user_instruction or "Không có yêu cầu bổ sung"}

Phong cách cần tuân thủ:
{style_hint}

Yêu cầu bắt buộc:
- Mỗi gợi ý phải là một phiên bản đầy đủ của toàn bài, không chỉ một câu lẻ.
- Giữ nguyên thông tin cốt lõi và trình tự ý chính của bài gốc.
- Có thể diễn đạt lại để bài mạch lạc, rõ ràng và tự nhiên hơn.
- Độ dài mỗi gợi ý khoảng {min_words} đến {max_words} từ.
- Không dùng bullet, không thêm tiêu đề.
- Trả về JSON hợp lệ đúng cấu trúc dưới đây.

Định dạng JSON:
{{
    "suggestions": [
        {{"text": "bản chỉnh sửa toàn bài 1", "confidence": 0.9, "reason": "vì sao phiên bản này phù hợp"}},
        {{"text": "bản chỉnh sửa toàn bài 2", "confidence": 0.85, "reason": "vì sao phiên bản này phù hợp"}},
        {{"text": "bản chỉnh sửa toàn bài 3", "confidence": 0.8, "reason": "vì sao phiên bản này phù hợp"}}
    ]
}}"""

        if resolved_scope == "phrase":
            min_words = max(1, min(source_word_count + 1, max(1, suggestion_length - 2)))
            max_words = max(min_words + 1, min(18, max(source_word_count + 4, suggestion_length + 2)))

            return f"""Bạn là trợ lý viết tiếng Việt chuyên CHỈNH SỬA CỤM TỪ trong câu.
Nhiệm vụ: đề xuất {max_suggestions} phiên bản viết lại tốt hơn cho phần văn bản đã chọn, nhưng vẫn GIỮ NGUYÊN ý nghĩa.

Ngữ cảnh xung quanh (chỉ để hiểu văn phong):
{context}

Phần được chọn để chỉnh sửa:
{source_text}

Yêu cầu chỉnh sửa của người dùng (ưu tiên cao):
{user_instruction or "Không có yêu cầu bổ sung"}

Phong cách cần tuân thủ:
{style_hint}

Yêu cầu bắt buộc:
- CHỈ trả về cụm thay thế cho phần đã chọn, KHÔNG viết lại toàn câu hoặc toàn đoạn.
- Tuyệt đối không đổi nghĩa, không thêm thông tin mới làm lệch ý gốc.
- Độ dài gần tương đương phần gốc, khoảng {min_words} đến {max_words} từ.
- Không dùng bullet, không thêm tiêu đề.
- Trả về JSON hợp lệ đúng cấu trúc dưới đây.

Định dạng JSON:
{{
    "suggestions": [
        {{"text": "cụm thay thế 1", "confidence": 0.9, "reason": "vì sao cách diễn đạt này phù hợp"}},
        {{"text": "cụm thay thế 2", "confidence": 0.85, "reason": "vì sao cách diễn đạt này phù hợp"}},
        {{"text": "cụm thay thế 3", "confidence": 0.8, "reason": "vì sao cách diễn đạt này phù hợp"}}
    ]
}}"""

        return f"""Bạn là trợ lý viết tiếng Việt chuyên VIẾT LẠI CÂU theo ngữ cảnh.
Nhiệm vụ: đề xuất {max_suggestions} phiên bản viết lại hay hơn cho câu gốc, nhưng vẫn GIỮ NGUYÊN ý nghĩa.

Ngữ cảnh xung quanh (để hiểu văn phong):
{context}

Câu gốc cần viết lại:
{source_text}

Yêu cầu chỉnh sửa của người dùng (ưu tiên cao):
{user_instruction or "Không có yêu cầu bổ sung"}

Phong cách cần tuân thủ:
{style_hint}

Yêu cầu bắt buộc:
- Tuyệt đối không đổi nghĩa, không thêm thông tin mới làm lệch mục đích câu nói.
- Ưu tiên bám sát yêu cầu chỉnh sửa của người dùng nếu có.
- Giữ mạch ngữ cảnh và giọng văn nhất quán với đoạn hiện tại.
- Viết tự nhiên, tránh mẫu khuôn sáo lặp lại.
- Mỗi gợi ý là 1 câu hoàn chỉnh, khoảng {max(10, suggestion_length)} đến {max(18, suggestion_length + 10)} từ.
- Không dùng bullet, không thêm tiêu đề.
- Trả về JSON hợp lệ đúng cấu trúc dưới đây.

Định dạng JSON:
{{
    "suggestions": [
        {{"text": "câu viết lại 1", "confidence": 0.9, "reason": "vì sao cách diễn đạt này tốt hơn"}},
        {{"text": "câu viết lại 2", "confidence": 0.85, "reason": "vì sao cách diễn đạt này tốt hơn"}},
        {{"text": "câu viết lại 3", "confidence": 0.8, "reason": "vì sao cách diễn đạt này tốt hơn"}}
    ]
}}"""

    def _build_continuation_prompt(
        self,
        context: str,
        max_suggestions: int,
        suggestion_length: int,
        instruction: Optional[str],
    ) -> str:
        user_instruction = (instruction or "").strip()
        style_hint = self._resolve_style_hint(user_instruction)
        last_line = self._extract_last_sentence(context)

        return f"""Bạn là trợ lý viết tiếng Việt chuyên gợi ý VIẾT TIẾP.
Nhiệm vụ: đọc ngữ cảnh và đề xuất {max_suggestions} câu/đoạn NGẮN để tiếp nối mạch ý.

Dòng người dùng vừa nhập (ưu tiên cao nhất):
{last_line or context}

Ngữ cảnh đầy đủ:
{context}

Yêu cầu của người dùng (ưu tiên cao):
{user_instruction or "Viết tiếp tự nhiên, logic, không lặp"}

Phong cách cần tuân thủ:
{style_hint}

Yêu cầu bắt buộc:
- Mỗi gợi ý phải nối trực tiếp từ dòng người dùng vừa nhập.
- Mỗi gợi ý phải phản hồi đúng yêu cầu người dùng đã nêu.
- Chỉ viết phần tiếp theo, không lặp lại hoặc diễn giải lại nguyên văn ngữ cảnh.
- Mỗi gợi ý dài khoảng {max(6, suggestion_length)} đến {max(12, suggestion_length + 8)} từ.
- Không dùng bullet, không thêm tiêu đề.
- Trả về JSON hợp lệ đúng cấu trúc dưới đây.

Định dạng JSON:
{{
    "suggestions": [
        {{"text": "phần viết tiếp 1", "confidence": 0.9, "reason": "bám dòng nhập và yêu cầu người dùng"}},
        {{"text": "phần viết tiếp 2", "confidence": 0.8, "reason": "bám dòng nhập và yêu cầu người dùng"}},
        {{"text": "phần viết tiếp 3", "confidence": 0.7, "reason": "bám dòng nhập và yêu cầu người dùng"}}
    ]
}}"""

    def _extract_json_object(self, text: str) -> Optional[str]:
        """Trích xuất object JSON đầu tiên cân bằng ngoặc từ chuỗi phản hồi."""
        if not text:
            return None

        stripped = text.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            return stripped

        start = None
        depth = 0
        for idx, ch in enumerate(text):
            if ch == "{":
                if start is None:
                    start = idx
                depth += 1
            elif ch == "}" and start is not None:
                depth -= 1
                if depth == 0:
                    return text[start:idx + 1]
        return None

    def _parse_suggestions_payload(self, response_text: str) -> List[Dict[str, Any]]:
        """Parse phản hồi model thành danh sách suggestions thống nhất."""
        json_blob = self._extract_json_object(response_text)
        if not json_blob:
            return []

        try:
            parsed = json.loads(json_blob)
        except Exception:
            return []

        raw_suggestions = []
        if isinstance(parsed, dict):
            raw_suggestions = parsed.get("suggestions", [])
        elif isinstance(parsed, list):
            raw_suggestions = parsed

        normalized: List[Dict[str, Any]] = []
        if not isinstance(raw_suggestions, list):
            return normalized

        for item in raw_suggestions:
            if isinstance(item, str):
                normalized.append({
                    "text": item,
                    "confidence": 0.75,
                    "reason": "Gợi ý từ mô hình AI"
                })
                continue

            if isinstance(item, dict):
                text = str(item.get("text", "")).strip()
                if not text:
                    continue
                normalized.append({
                    "text": text,
                    "confidence": float(item.get("confidence", 0.75)),
                    "reason": item.get("reason", "Gợi ý từ mô hình AI")
                })

        return normalized

    def _normalize_suggestions(
        self,
        context: str,
        suggestions: List[Dict],
        max_suggestions: int,
        instruction: Optional[str] = None
    ) -> List[Dict]:
        """Chuẩn hóa để đảm bảo gợi ý là phần tiếp theo, không lặp lại ngữ cảnh."""
        normalized: List[Dict] = []
        seen = set()
        context_norm = re.sub(r'\s+', ' ', (context or '')).strip().lower()

        for item in suggestions:
            text = str(item.get("text", "")).strip().strip('"')
            if not text:
                continue

            text_norm = re.sub(r'\s+', ' ', text).strip().lower()
            if not text_norm:
                continue
            if text_norm in seen:
                continue
            # Tránh trường hợp mô hình lặp lại nguyên ngữ cảnh hoặc đoạn cuối
            if context_norm and (text_norm in context_norm or context_norm.endswith(text_norm)):
                continue

            seen.add(text_norm)
            normalized.append({
                "text": text,
                "confidence": float(item.get("confidence", 0.75)),
                "reason": item.get("reason", "Gợi ý tiếp nối theo ngữ cảnh")
            })

            if len(normalized) >= max_suggestions:
                break

        if normalized:
            return normalized

        return self._get_mock_suggestions(
            context=context,
            max_suggestions=max_suggestions,
            mode="continuation",
            instruction=instruction
        )

    def _normalize_rewrite_suggestions(
        self,
        context: str,
        original_text: str,
        suggestions: List[Dict],
        max_suggestions: int,
        instruction: Optional[str] = None,
        rewrite_scope: Optional[str] = None
    ) -> List[Dict]:
        """Chuẩn hóa gợi ý rewrite: giữ ý, khác cách diễn đạt, không lặp khuôn mẫu."""
        normalized: List[Dict] = []
        seen = set()
        source = re.sub(r'\s+', ' ', (original_text or '').strip()).lower()
        source_tokens = set(re.findall(r'\w+', source))
        resolved_scope = self._resolve_rewrite_scope(original_text, rewrite_scope)
        source_word_count = len(source_tokens)
        source_has_terminal_punctuation = bool(re.search(r'[.!?…]["\')\]]*$', (original_text or '').strip()))

        for item in suggestions:
            text = str(item.get("text", "")).strip().strip('"')
            if not text:
                continue

            text_norm = re.sub(r'\s+', ' ', text).strip().lower()
            if not text_norm or text_norm in seen:
                continue

            # Loại trường hợp trả y nguyên câu gốc
            if source and text_norm == source:
                continue

            if resolved_scope == "phrase":
                if "\n" in text:
                    continue

                candidate_word_count = len(re.findall(r'\w+', text_norm))
                min_words = max(1, source_word_count - 3)
                max_words = max(min_words + 1, min(18, source_word_count + 6))
                if source_word_count and (candidate_word_count < min_words or candidate_word_count > max_words):
                    continue

                candidate_has_terminal_punctuation = bool(re.search(r'[.!?…]["\')\]]*$', text.strip()))
                if not source_has_terminal_punctuation and candidate_has_terminal_punctuation:
                    continue
            elif resolved_scope == "document":
                candidate_word_count = len(re.findall(r'\w+', text_norm))
                min_words = max(24, int(source_word_count * 0.35) if source_word_count else 24)
                max_words = max(min_words + 30, int(source_word_count * 1.9) if source_word_count else 300)
                if candidate_word_count < min_words or candidate_word_count > max_words:
                    continue

            # Giữ mức độ gần nghĩa cơ bản bằng overlap từ khóa, nhưng vẫn cho phép diễn đạt linh hoạt
            candidate_tokens = set(re.findall(r'\w+', text_norm))
            if source_tokens and candidate_tokens:
                overlap_ratio = len(source_tokens & candidate_tokens) / max(1, len(source_tokens))
                if resolved_scope == "phrase":
                    min_overlap_ratio = 0.12
                elif resolved_scope == "document":
                    min_overlap_ratio = 0.08
                else:
                    min_overlap_ratio = 0.2
                if overlap_ratio < min_overlap_ratio:
                    continue

            seen.add(text_norm)
            normalized.append({
                "text": text,
                "confidence": float(item.get("confidence", 0.82)),
                "reason": item.get("reason", "Viết lại tự nhiên hơn nhưng giữ nguyên ý chính")
            })

            if len(normalized) >= max_suggestions:
                break

        if normalized:
            return normalized

        return self._get_mock_suggestions(
            context=context,
            max_suggestions=max_suggestions,
            mode="rewrite",
            original_text=original_text,
            instruction=instruction,
            rewrite_scope=rewrite_scope
        )

    def _get_mock_suggestions(
        self,
        context: str,
        max_suggestions: int,
        mode: str = "continuation",
        original_text: Optional[str] = None,
        instruction: Optional[str] = None,
        rewrite_scope: Optional[str] = None
    ) -> List[Dict]:
        """Trả về gợi ý fallback bám theo câu cuối để vẫn có tính tiếp nối."""
        instruction_text = (instruction or "").strip()
        style_hint = self._resolve_style_hint(instruction_text)

        if (mode or "continuation").strip().lower() == "rewrite":
            base = (original_text or context or "").strip()
            if base:
                resolved_scope = self._resolve_rewrite_scope(base, rewrite_scope)
                rewrite_candidates = self._build_local_rewrite_candidates(
                    base_text=base,
                    instruction=instruction_text,
                    scope=resolved_scope,
                    max_suggestions=max_suggestions
                )

                scope_label = {
                    "phrase": "phần đã chọn",
                    "sentence": "đoạn đã chọn",
                    "document": "toàn bài"
                }.get(resolved_scope, "nội dung")

                suggestions = []
                for index, candidate in enumerate(rewrite_candidates[:max_suggestions]):
                    confidence = max(0.38, 0.56 - index * 0.06)
                    if index == 0:
                        reason = f"Fallback local cho {scope_label}: đã viết lại theo yêu cầu người dùng và giữ ý chính."
                    elif index == 1:
                        reason = f"Biến thể phong cách cho {scope_label}; định hướng: {style_hint}"
                    else:
                        reason = f"Biến thể diễn đạt khác cho {scope_label}{'; bám yêu cầu: ' + instruction_text if instruction_text else ''}"

                    suggestions.append({
                        "text": candidate,
                        "confidence": confidence,
                        "reason": reason
                    })

                if suggestions:
                    return suggestions

        last_sentence = self._extract_last_sentence(context)
        context_lower = (context or "").lower()
        focus = " ".join(last_sentence.split()[-4:]).strip()
        focus_hint = f"{focus} " if focus else ""
        instruction_reason = f"; bám yêu cầu: {instruction_text}" if instruction_text else ""

        is_job_context = any(
            key in context_lower
            for key in ["ứng tuyển", "công ty", "vị trí", "cv", "phỏng vấn", "việc làm"]
        )

        if is_job_context:
            candidates = [
                {
                    "text": f"Tiếp theo, {focus_hint}để thể hiện rõ vị trí mong muốn và lý do bạn phù hợp.",
                    "confidence": 0.74,
                    "reason": f"Bám ngữ cảnh ứng tuyển việc làm{instruction_reason}"
                },
                {
                    "text": "Bạn có thể nêu ngắn gọn kinh nghiệm nổi bật và giá trị bạn có thể đóng góp cho công ty.",
                    "confidence": 0.7,
                    "reason": f"Làm rõ năng lực theo ngữ cảnh ứng tuyển{instruction_reason}"
                },
                {
                    "text": "Kết lại bằng mong muốn được trao đổi thêm trong buổi phỏng vấn để trình bày kỹ hơn.",
                    "confidence": 0.66,
                    "reason": f"Giữ mạch chuyên nghiệp và hướng đến hành động tiếp theo{instruction_reason}"
                },
            ]
            return candidates[:max_suggestions]

        candidates = [
            {
                "text": f"Tiếp theo, {focus_hint}được làm rõ hơn bằng một ví dụ cụ thể để người đọc dễ hình dung.",
                "confidence": 0.72,
                "reason": f"Mở rộng ý vừa nêu bằng ví dụ{instruction_reason}"
            },
            {
                "text": f"Từ đó, có thể thấy luận điểm này liên kết chặt với mục tiêu chung của nội dung bạn đang trình bày.",
                "confidence": 0.68,
                "reason": f"Kết nối mạch ý hiện tại{instruction_reason}"
            },
            {
                "text": "Một hướng tiếp theo phù hợp là nêu ngắn gọn tác động thực tế, rồi chuyển sang phần giải pháp hoặc kết luận.",
                "confidence": 0.64,
                "reason": f"Giữ cấu trúc mạch lạc{instruction_reason}"
            },
        ]
        return candidates[:max_suggestions]

    def _extract_instruction_flags(self, instruction: Optional[str]) -> Dict[str, bool]:
        text = (instruction or "").strip().lower()
        return {
            "formal": any(key in text for key in ["trang trọng", "chuyên nghiệp", "formal", "lịch sự"]),
            "concise": any(key in text for key in ["ngắn", "súc tích", "rút gọn", "gọn"]),
            "expand": any(key in text for key in ["dài", "chi tiết", "mở rộng"]),
            "coherent": any(key in text for key in ["mạch lạc", "logic", "rõ ràng", "liên kết"]),
            "friendly": any(key in text for key in ["thân thiện", "gần gũi"]),
        }

    def _cleanup_generated_text(self, text: str) -> str:
        normalized = re.sub(r'\s+', ' ', (text or '')).strip()
        normalized = re.sub(r'\s+([,.;!?…])', r'\1', normalized)
        normalized = re.sub(r'([,.;!?…])([A-Za-zÀ-ỹ])', r'\1 \2', normalized)
        normalized = re.sub(r'\s{2,}', ' ', normalized)
        return normalized.strip(' ,')

    def _rewrite_local_variant(self, text: str, instruction: str, scope: str, variant: int) -> str:
        flags = self._extract_instruction_flags(instruction)
        rewritten = self._cleanup_generated_text(text)

        if flags["formal"]:
            formal_map = [
                (r'\bMình\b', 'Tôi'),
                (r'\bmình\b', 'tôi'),
                (r'\bhơi\b', 'khá'),
                (r'\bcái\b', ''),
                (r'\brất mong\b', 'mong muốn')
            ]
            for pattern, replacement in formal_map:
                rewritten = re.sub(pattern, replacement, rewritten)

        if flags["friendly"] and not flags["formal"]:
            rewritten = re.sub(r'\bTôi\b', 'Mình', rewritten)
            rewritten = re.sub(r'\btôi\b', 'mình', rewritten)

        if flags["concise"]:
            concise_remove = [
                r'\bhiện tại\b',
                r'\bđặc biệt\b',
                r'\bmột số\b',
                r'\bcũng\b',
                r'\bkhá\b'
            ]
            for pattern in concise_remove:
                rewritten = re.sub(pattern, '', rewritten, flags=re.IGNORECASE)

        if flags["coherent"]:
            rewritten = re.sub(r'\bnhưng\b', 'tuy nhiên', rewritten, count=1, flags=re.IGNORECASE)
            rewritten = re.sub(r'\bvà\b', 'đồng thời', rewritten, count=1, flags=re.IGNORECASE)

        if flags["expand"] and scope in {"phrase", "sentence"} and variant == 2:
            rewritten = f"{rewritten} để thể hiện rõ hơn định hướng phát triển."

        if scope == "document":
            sentences = [s.strip() for s in re.split(r'(?<=[.!?…])\s+', rewritten) if s.strip()]
            if len(sentences) > 1:
                if variant == 0:
                    starters = ["Trước hết, ", "Bên cạnh đó, ", "Cuối cùng, "]
                elif variant == 1:
                    starters = ["Về tổng thể, ", "Ngoài ra, ", "Từ đó, "]
                else:
                    starters = ["Ở hiện tại, ", "Đồng thời, ", "Nhờ vậy, "]

                rebuilt = []
                for idx, sentence in enumerate(sentences):
                    starter = starters[min(idx, len(starters) - 1)] if idx < 3 else ""
                    if re.match(r'^(Trước hết|Bên cạnh đó|Cuối cùng|Ngoài ra|Từ đó|Đồng thời|Ở hiện tại|Nhờ vậy),', sentence):
                        rebuilt.append(sentence)
                    else:
                        rebuilt.append(f"{starter}{sentence}".strip())
                rewritten = " ".join(rebuilt)

        if variant == 1:
            rewritten = re.sub(r'\bđang\b', 'hiện đang', rewritten, count=1, flags=re.IGNORECASE)
            rewritten = re.sub(r'\btìm hiểu\b', 'nghiên cứu', rewritten, count=1, flags=re.IGNORECASE)
        elif variant == 2:
            rewritten = re.sub(r'\bquan tâm\b', 'chú trọng', rewritten, count=1, flags=re.IGNORECASE)
            rewritten = re.sub(r'\bnâng cao\b', 'phát triển', rewritten, count=1, flags=re.IGNORECASE)

        return self._cleanup_generated_text(rewritten)

    def _build_local_rewrite_candidates(
        self,
        base_text: str,
        instruction: str,
        scope: str,
        max_suggestions: int
    ) -> List[str]:
        source = self._cleanup_generated_text(base_text)
        if not source:
            return []

        variants = []
        for variant in range(5):
            candidate = self._rewrite_local_variant(source, instruction, scope, variant)
            if candidate:
                variants.append(candidate)

        # Keep unique candidates while preserving order.
        unique_candidates: List[str] = []
        seen = set()
        for candidate in variants:
            key = candidate.lower()
            if key in seen:
                continue
            seen.add(key)
            unique_candidates.append(candidate)
            if len(unique_candidates) >= max(3, max_suggestions):
                break

        while len(unique_candidates) < max_suggestions:
            unique_candidates.append(source)

        return unique_candidates[:max_suggestions]

    async def _get_openai_suggestions(
        self,
        context: str,
        max_suggestions: int,
        suggestion_length: int,
        mode: str = "continuation",
        original_text: Optional[str] = None,
        instruction: Optional[str] = None,
        rewrite_scope: Optional[str] = None,
        provider_override: Optional[str] = None,
        client_override: Optional[Any] = None,
        strict_errors: bool = False,
    ) -> List[Dict]:
        """Lấy suggestions từ OpenAI"""
        provider_name = (provider_override or self.api_provider or "openai").strip().lower()
        client = client_override or self.client

        try:
            if client is None:
                if strict_errors:
                    raise RuntimeError(f"AI client for provider '{provider_name}' is not initialized")
                return self._get_mock_suggestions(
                    context=context,
                    max_suggestions=max_suggestions,
                    mode=mode,
                    original_text=original_text,
                    instruction=instruction,
                    rewrite_scope=rewrite_scope
                )

            mode_normalized = (mode or "continuation").strip().lower()
            if mode_normalized == "rewrite":
                prompt = self._build_rewrite_prompt(
                    context=context,
                    max_suggestions=max_suggestions,
                    suggestion_length=suggestion_length,
                    original_text=original_text,
                    instruction=instruction,
                    rewrite_scope=rewrite_scope,
                )
            else:
                prompt = self._build_continuation_prompt(
                    context=context,
                    max_suggestions=max_suggestions,
                    suggestion_length=suggestion_length,
                    instruction=instruction,
                )

            if provider_name == "openrouter":
                merged_prompt = (
                    "Bạn là trợ lý tiếng Việt. "
                    + ("Chuyên viết lại câu giữ nguyên ý nghĩa theo ngữ cảnh. " if mode_normalized == "rewrite" else "Chuyên viết tiếp mạch nội dung. ")
                    + "Luôn trả về JSON hợp lệ.\n\n"
                    + prompt
                )
                messages = [{"role": "user", "content": merged_prompt}]
            else:
                messages = [
                    {
                        "role": "system",
                        "content": (
                            "Bạn là trợ lý tiếng Việt.")
                            + (" Chuyên viết lại câu giữ nguyên ý nghĩa theo ngữ cảnh." if mode_normalized == "rewrite" else " Chuyên viết tiếp mạch nội dung.")
                            + " Luôn trả về JSON hợp lệ."
                    },
                    {"role": "user", "content": prompt}
                ]

            if provider_name == "openrouter":
                if getattr(settings, "OPENROUTER_AUTO_FAILOVER", True):
                    candidate_models = self._get_openrouter_candidate_models()
                else:
                    candidate_models = [self._get_active_model_for_provider("openrouter")]
            elif provider_name == "openai":
                if self.failover_enabled:
                    candidate_models = self._get_openai_candidate_models()
                else:
                    candidate_models = [self._get_active_model_for_provider("openai")]
            else:
                candidate_models = [self._get_active_model_for_provider(provider_name)]

            last_error: Optional[Exception] = None
            for model_name in candidate_models:
                try:
                    response = await client.chat.completions.create(
                        model=model_name,
                        messages=messages,
                        temperature=0.7,
                        max_tokens=400
                    )

                    response_text = response.choices[0].message.content
                    suggestions = self._parse_suggestions_payload(response_text)
                    if not suggestions:
                        logger.warning(f"Model {model_name} returned empty/invalid suggestion payload, trying next model")
                        continue

                    logger.info(f"Composer AI succeeded with model: {model_name}")
                    if mode_normalized == "rewrite":
                        return self._normalize_rewrite_suggestions(
                            context=context,
                            original_text=original_text or context,
                            suggestions=suggestions,
                            max_suggestions=max_suggestions,
                            instruction=instruction,
                            rewrite_scope=rewrite_scope,
                        )
                    return self._normalize_suggestions(
                        context=context,
                        suggestions=suggestions,
                        max_suggestions=max_suggestions,
                        instruction=instruction,
                    )
                except Exception as model_error:
                    last_error = model_error
                    logger.warning(f"Composer AI model failed [{provider_name}/{model_name}]: {model_error}")
                    if self._is_auth_error(model_error):
                        # Auth/account issues are provider-level; try next provider instead.
                        break
                    if self._is_quota_or_rate_error(model_error) and provider_name == "openai":
                        # OpenAI quota/rate limit errors are typically account-level.
                        break
                    continue

            if last_error:
                if strict_errors:
                    raise last_error
                raise last_error

            return self._get_mock_suggestions(
                context=context,
                max_suggestions=max_suggestions,
                mode=mode_normalized,
                original_text=original_text,
                instruction=instruction,
                rewrite_scope=rewrite_scope
            )

        except Exception as e:
            if strict_errors:
                raise

            logger.error(f"Chat API error ({provider_name}): {e}")
            return self._get_mock_suggestions(
                context=context,
                max_suggestions=max_suggestions,
                mode=mode,
                original_text=original_text,
                instruction=instruction,
                rewrite_scope=rewrite_scope
            )

    async def _get_gemini_suggestions(
        self,
        context: str,
        max_suggestions: int,
        suggestion_length: int,
        mode: str = "continuation",
        original_text: Optional[str] = None,
        instruction: Optional[str] = None,
        rewrite_scope: Optional[str] = None
    ) -> List[Dict]:
        """Lấy suggestions từ Gemini (synchronous wrapper)"""
        try:
            mode_normalized = (mode or "continuation").strip().lower()
            if mode_normalized == "rewrite":
                prompt = self._build_rewrite_prompt(
                    context=context,
                    max_suggestions=max_suggestions,
                    suggestion_length=suggestion_length,
                    original_text=original_text,
                    instruction=instruction,
                    rewrite_scope=rewrite_scope,
                )
            else:
                prompt = self._build_continuation_prompt(
                    context=context,
                    max_suggestions=max_suggestions,
                    suggestion_length=suggestion_length,
                    instruction=instruction,
                )

            # Run in thread pool since Gemini API is sync
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.client.generate_content(prompt)
            )
            
            response_text = response.text
            suggestions = self._parse_suggestions_payload(response_text)
            if suggestions:
                if mode_normalized == "rewrite":
                    return self._normalize_rewrite_suggestions(
                        context=context,
                        original_text=original_text or context,
                        suggestions=suggestions,
                        max_suggestions=max_suggestions,
                        instruction=instruction,
                        rewrite_scope=rewrite_scope,
                    )
                return self._normalize_suggestions(
                    context=context,
                    suggestions=suggestions,
                    max_suggestions=max_suggestions,
                    instruction=instruction,
                )

            return self._get_mock_suggestions(
                context=context,
                max_suggestions=max_suggestions,
                mode=mode_normalized,
                original_text=original_text,
                instruction=instruction,
                rewrite_scope=rewrite_scope
            )

        except Exception as e:
            logger.error(f"Gemini API error: {e}")
            return self._get_mock_suggestions(
                context=context,
                max_suggestions=max_suggestions,
                mode=mode,
                original_text=original_text,
                instruction=instruction,
                rewrite_scope=rewrite_scope
            )

    def save_composition_action(
        self,
        db: Session,
        document_id: int,
        user_id: int,
        action_type: str,
        suggested_text: Optional[str] = None,
        original_text: Optional[str] = None,
        modified_text: Optional[str] = None,
        context: Optional[str] = None,
        accepted: int = 0
    ) -> Dict:
        """
        Lưu lịch sử soạn thảo (suggestion, edit, acceptance)

        Args:
            db: Database session
            document_id: ID của document
            user_id: ID của user
            action_type: 'suggestion', 'edit', 'acceptance'
            suggested_text: Text được gợi ý
            original_text: Text gốc
            modified_text: Text sau khi edit
            context: Ngữ cảnh
            accepted: 1 nếu accept, 0 nếu reject

        Returns:
            Dict với thông tin được lưu
        """
        try:
            history = CompositionHistory(
                document_id=document_id,
                user_id=user_id,
                action_type=action_type,
                suggested_text=suggested_text,
                original_text=original_text,
                modified_text=modified_text,
                context=context,
                accepted=accepted
            )
            db.add(history)
            db.commit()
            db.refresh(history)

            logger.info(f"Saved composition history: {history.id}")
            return {
                "id": history.id,
                "action_type": action_type,
                "created_at": history.created_at.isoformat()
            }

        except Exception as e:
            logger.error(f"Error saving composition history: {e}")
            db.rollback()
            return {}

    def save_document(
        self,
        db: Session,
        user_id: int,
        title: str,
        content: str,
        description: Optional[str] = None,
        document_id: Optional[int] = None
    ) -> Dict:
        """
        Lưu hoặc cập nhật document

        Args:
            db: Database session
            user_id: ID của user
            title: Tiêu đề tài liệu
            content: Nội dung tài liệu
            description: Mô tả tài liệu
            document_id: ID của document (nếu update)

        Returns:
            Dict với thông tin document
        """
        try:
            if document_id:
                # Cập nhật document hiện có
                doc = db.query(Document).filter(
                    Document.id == document_id,
                    Document.user_id == user_id
                ).first()
                
                if not doc:
                    logger.error(f"Document {document_id} not found")
                    return {}

                doc.title = title
                doc.content = content
                if description:
                    doc.description = description
                doc.updated_at = datetime.utcnow()
            else:
                # Tạo document mới
                doc = Document(
                    user_id=user_id,
                    title=title,
                    content=content,
                    description=description
                )
                db.add(doc)

            db.commit()
            db.refresh(doc)

            logger.info(f"Saved document: {doc.id}")
            return {
                "id": doc.id,
                "title": doc.title,
                "created_at": doc.created_at.isoformat(),
                "updated_at": doc.updated_at.isoformat()
            }

        except Exception as e:
            logger.error(f"Error saving document: {e}")
            db.rollback()
            return {}

    def get_document(
        self,
        db: Session,
        user_id: int,
        document_id: int
    ) -> Optional[Dict]:
        """
        Lấy document theo ID

        Args:
            db: Database session
            user_id: ID của user
            document_id: ID của document

        Returns:
            Dict với thông tin document hoặc None
        """
        try:
            doc = db.query(Document).filter(
                Document.id == document_id,
                Document.user_id == user_id
            ).first()

            if not doc:
                return None

            return {
                "id": doc.id,
                "title": doc.title,
                "content": doc.content,
                "description": doc.description,
                "created_at": doc.created_at.isoformat(),
                "updated_at": doc.updated_at.isoformat()
            }

        except Exception as e:
            logger.error(f"Error getting document: {e}")
            return None

    def list_documents(
        self,
        db: Session,
        user_id: int,
        limit: int = 20,
        offset: int = 0
    ) -> List[Dict]:
        """
        Liệt kê documents của user

        Args:
            db: Database session
            user_id: ID của user
            limit: Số lượng tối đa
            offset: Vị trí bắt đầu

        Returns:
            Danh sách dict chứa thông tin documents
        """
        try:
            docs = db.query(Document).filter(
                Document.user_id == user_id
            ).order_by(
                Document.updated_at.desc()
            ).offset(offset).limit(limit).all()

            return [
                {
                    "id": doc.id,
                    "title": doc.title,
                    "description": doc.description,
                    "created_at": doc.created_at.isoformat(),
                    "updated_at": doc.updated_at.isoformat(),
                    "content_preview": doc.content[:100] + "..." if len(doc.content) > 100 else doc.content
                }
                for doc in docs
            ]

        except Exception as e:
            logger.error(f"Error listing documents: {e}")
            return []

    def delete_document(
        self,
        db: Session,
        user_id: int,
        document_id: int
    ) -> bool:
        """
        Xóa document

        Args:
            db: Database session
            user_id: ID của user
            document_id: ID của document

        Returns:
            True nếu thành công, False nếu thất bại
        """
        try:
            doc = db.query(Document).filter(
                Document.id == document_id,
                Document.user_id == user_id
            ).first()

            if not doc:
                return False

            db.delete(doc)
            db.commit()

            logger.info(f"Deleted document: {document_id}")
            return True

        except Exception as e:
            logger.error(f"Error deleting document: {e}")
            db.rollback()
            return False
