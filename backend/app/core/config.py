import os
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Database Configuration
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "mysql+pymysql://root@localhost:3306/autofill_db"
    )
    SQL_ECHO: bool = os.getenv("SQL_ECHO", "false").lower() in {"1", "true", "yes", "on"}
    
    # API Configuration
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "AutoFill AI System"
    APP_BASE_URL: str = os.getenv("APP_BASE_URL", "http://localhost:8000")
    
    # CORS Configuration
    BACKEND_CORS_ORIGINS: list = [
        "http://localhost",
        "http://localhost:3000",
        "http://localhost:8000",
        "http://127.0.0.1",
        "http://127.0.0.1:8000",
        "http://127.0.0.1:3000",
    ]
    
    # AI Configuration
    TOP_SUGGESTIONS_COUNT: int = 3
    HISTORY_LOOKBACK_DAYS: int = 30
    MIN_HISTORY_ENTRIES: int = 5
    
    # AI Composer Configuration
    AI_PROVIDER: str = os.getenv("AI_PROVIDER", "openai")  # 'openai' | 'gemini' | 'openrouter'
    AI_PROFILE: str = os.getenv("AI_PROFILE", "auto")  # auto | openrouter | openai | gemini
    AI_FAILOVER_ENABLED: bool = os.getenv("AI_FAILOVER_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
    AI_API_KEY: str = os.getenv("AI_API_KEY", "")
    AI_MODEL: str = os.getenv("AI_MODEL", "gpt-4o-mini")

    # Google Gemini Configuration
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

    # OpenAI Configuration
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    OPENAI_FALLBACK_MODELS: str = os.getenv("OPENAI_FALLBACK_MODELS", "gpt-4o-mini,gpt-4o,gpt-3.5-turbo")

    # OpenRouter Configuration (OpenAI-compatible endpoint)
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
    OPENROUTER_MODEL: str = os.getenv("OPENROUTER_MODEL", "openai/gpt-oss-120b:free")
    OPENROUTER_FALLBACK_MODELS: str = os.getenv("OPENROUTER_FALLBACK_MODELS", "")
    OPENROUTER_AUTO_FAILOVER: bool = os.getenv("OPENROUTER_AUTO_FAILOVER", "true").lower() in {"1", "true", "yes", "on"}
    OPENROUTER_BASE_URL: str = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    OPENROUTER_SITE_URL: str = os.getenv("OPENROUTER_SITE_URL", "http://localhost:8000")
    OPENROUTER_APP_NAME: str = os.getenv("OPENROUTER_APP_NAME", "AutoFill AI System")

    COMPOSER_MAX_SUGGESTIONS: int = 3
    COMPOSER_SUGGESTION_LENGTH: int = 10

    # Email verification / SMTP
    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USERNAME: str = os.getenv("SMTP_USERNAME", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM_EMAIL: str = os.getenv("SMTP_FROM_EMAIL", "")
    SMTP_FROM_NAME: str = os.getenv("SMTP_FROM_NAME", "AutoFill AI")
    SMTP_USE_TLS: bool = os.getenv("SMTP_USE_TLS", "true").lower() in {"1", "true", "yes", "on"}
    SMTP_USE_SSL: bool = os.getenv("SMTP_USE_SSL", "false").lower() in {"1", "true", "yes", "on"}
    EMAIL_VERIFICATION_TTL_HOURS: int = int(os.getenv("EMAIL_VERIFICATION_TTL_HOURS", "24"))
    # When SMTP is missing: auto-verify on signup so local dev still works (set false in production)
    EMAIL_AUTO_VERIFY_WITHOUT_SMTP: bool = os.getenv(
        "EMAIL_AUTO_VERIFY_WITHOUT_SMTP", "true"
    ).lower() in {"1", "true", "yes", "on"}

    # RAG / semantic memory (embeddings stored in DB; cosine search in-process)
    RAG_ENABLED: bool = os.getenv("RAG_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
    RAG_EMBEDDING_PROVIDER: str = os.getenv("RAG_EMBEDDING_PROVIDER", "")  # openai | gemini | (empty=auto)
    RAG_EMBEDDING_MODEL: str = os.getenv("RAG_EMBEDDING_MODEL", "text-embedding-3-small")
    RAG_SEMANTIC_TOP_K: int = int(os.getenv("RAG_SEMANTIC_TOP_K", "5"))
    RAG_MIN_SIMILARITY: float = float(os.getenv("RAG_MIN_SIMILARITY", "0.55"))
    RAG_HIGH_SIMILARITY: float = float(os.getenv("RAG_HIGH_SIMILARITY", "0.72"))
    RAG_SKIP_IF_STRONG_MEMORY: bool = os.getenv("RAG_SKIP_IF_STRONG_MEMORY", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    RAG_MAX_CHUNKS_SCAN: int = int(os.getenv("RAG_MAX_CHUNKS_SCAN", "2000"))
    RAG_CHUNK_CHAR_LIMIT: int = int(os.getenv("RAG_CHUNK_CHAR_LIMIT", "480"))
    RAG_CHUNK_OVERLAP: int = int(os.getenv("RAG_CHUNK_OVERLAP", "64"))
    RAG_EMBED_BATCH_SIZE: int = int(os.getenv("RAG_EMBED_BATCH_SIZE", "64"))
    RAG_INDEX_ON_UPLOAD: bool = os.getenv("RAG_INDEX_ON_UPLOAD", "true").lower() in {"1", "true", "yes", "on"}
    RAG_FILL_ON_EXPORT: bool = os.getenv("RAG_FILL_ON_EXPORT", "true").lower() in {"1", "true", "yes", "on"}

    # Word template intelligent parse (LLM reads full document structure)
    WORD_LLM_PARSE_ENABLED: bool = os.getenv("WORD_LLM_PARSE_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
    WORD_LLM_PARSE_MIN_FIELDS: int = int(os.getenv("WORD_LLM_PARSE_MIN_FIELDS", "12"))
    WORD_LLM_PARSE_TIMEOUT_SEC: int = int(os.getenv("WORD_LLM_PARSE_TIMEOUT_SEC", "45"))
    # Mẫu Sơ yếu lý lịch: dùng schema đầy đủ ngay (nhanh); bật TRY_LLM để LLM bổ sung thêm
    WORD_LLM_SYLL_USE_TEMPLATE: bool = os.getenv("WORD_LLM_SYLL_USE_TEMPLATE", "true").lower() in {"1", "true", "yes", "on"}
    WORD_LLM_SYLL_TRY_LLM: bool = os.getenv("WORD_LLM_SYLL_TRY_LLM", "false").lower() in {"1", "true", "yes", "on"}
    # Word upload: skip heavy LLM orchestrator (fields already parsed); index RAG in background
    WORD_SKIP_ORCHESTRATOR_ON_UPLOAD: bool = os.getenv(
        "WORD_SKIP_ORCHESTRATOR_ON_UPLOAD", "true"
    ).lower() in {"1", "true", "yes", "on"}

    # Excel vertical form parse (STT | Tên trường | Giá trị cần điền) + LLM
    EXCEL_LLM_PARSE_ENABLED: bool = os.getenv("EXCEL_LLM_PARSE_ENABLED", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    EXCEL_LLM_PARSE_MIN_FIELDS: int = int(os.getenv("EXCEL_LLM_PARSE_MIN_FIELDS", "3"))
    EXCEL_LLM_PARSE_TIMEOUT_SEC: int = int(os.getenv("EXCEL_LLM_PARSE_TIMEOUT_SEC", "45"))

    # Parse experiment logging (ground truth vs predictions)
    PARSE_EVAL_LOG_ENABLED: bool = os.getenv("PARSE_EVAL_LOG_ENABLED", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    PARSE_EVAL_DIR: str = os.getenv("PARSE_EVAL_DIR", "data/parse_eval")
    PARSE_PREDICTIONS_CSV: str = os.getenv("PARSE_PREDICTIONS_CSV", "")
    PARSE_LATENCY_CSV: str = os.getenv("PARSE_LATENCY_CSV", "")
    # Comma-separated formats excluded from parse EXP (default: pdf — chưa hỗ trợ thao tác PDF)
    PARSE_EVAL_SKIP_FORMATS: str = os.getenv("PARSE_EVAL_SKIP_FORMATS", "pdf")
    # Mỗi lần EXP parse → thư mục riêng: data/experiments/parse/<run_id>/
    PARSE_EXPERIMENTS_DIR: str = os.getenv("PARSE_EXPERIMENTS_DIR", "data/experiments/parse")
    # Opening a template: do not run per-field RAG hints (very slow for 50+ fields)
    WORD_TEMPLATE_LOAD_RAG_HINTS: bool = os.getenv("WORD_TEMPLATE_LOAD_RAG_HINTS", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    # Google OAuth (login with Google for existing accounts)
    GOOGLE_OAUTH_CLIENT_ID: str = os.getenv("GOOGLE_OAUTH_CLIENT_ID", os.getenv("GOOGLE_CLIENT_ID", ""))
    GOOGLE_OAUTH_CLIENT_SECRET: str = os.getenv(
        "GOOGLE_OAUTH_CLIENT_SECRET",
        os.getenv("GOOGLE_CLIENT_SECRET", ""),
    )
    GOOGLE_OAUTH_REDIRECT_URI: str = os.getenv(
        "GOOGLE_OAUTH_REDIRECT_URI",
        os.getenv("GOOGLE_REDIRECT_URI", "http://127.0.0.1:8000/api/auth/google/callback"),
    )
    GOOGLE_OAUTH_STATE_TTL_SECONDS: int = int(os.getenv("GOOGLE_OAUTH_STATE_TTL_SECONDS", "600"))
    
    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
