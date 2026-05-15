import os
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Database Configuration
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "mysql+pymysql://root@localhost:3306/autofill_db"
    )
    
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

    # RAG / semantic memory (embeddings stored in DB; cosine search in-process)
    RAG_ENABLED: bool = os.getenv("RAG_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
    RAG_EMBEDDING_MODEL: str = os.getenv("RAG_EMBEDDING_MODEL", "text-embedding-3-small")
    RAG_SEMANTIC_TOP_K: int = int(os.getenv("RAG_SEMANTIC_TOP_K", "5"))
    RAG_MAX_CHUNKS_SCAN: int = int(os.getenv("RAG_MAX_CHUNKS_SCAN", "2000"))
    RAG_CHUNK_CHAR_LIMIT: int = int(os.getenv("RAG_CHUNK_CHAR_LIMIT", "480"))
    RAG_CHUNK_OVERLAP: int = int(os.getenv("RAG_CHUNK_OVERLAP", "64"))
    RAG_EMBED_BATCH_SIZE: int = int(os.getenv("RAG_EMBED_BATCH_SIZE", "64"))

    # Google OAuth (login with Google for existing accounts)
    GOOGLE_OAUTH_CLIENT_ID: str = os.getenv("GOOGLE_OAUTH_CLIENT_ID", os.getenv("GOOGLE_CLIENT_ID", ""))
    GOOGLE_OAUTH_CLIENT_SECRET: str = os.getenv(
        "GOOGLE_OAUTH_CLIENT_SECRET",
        os.getenv("GOOGLE_CLIENT_SECRET", ""),
    )
    GOOGLE_OAUTH_REDIRECT_URI: str = os.getenv(
        "GOOGLE_OAUTH_REDIRECT_URI",
        os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/auth/google/callback"),
    )
    GOOGLE_OAUTH_STATE_TTL_SECONDS: int = int(os.getenv("GOOGLE_OAUTH_STATE_TTL_SECONDS", "600"))
    
    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
