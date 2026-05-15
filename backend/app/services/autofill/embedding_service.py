from __future__ import annotations

from app.core.config import settings
from app.core.logger import logger


def resolve_embedding_api_key() -> str:
    if settings.OPENAI_API_KEY.strip():
        return settings.OPENAI_API_KEY.strip()
    if settings.AI_PROVIDER.strip().lower() == "openai" and settings.AI_API_KEY.strip():
        return settings.AI_API_KEY.strip()
    return ""


def embed_texts_sync(texts: list[str]) -> list[list[float]]:
    """
    Batch embedding via OpenAI API. Returns empty list if RAG disabled or no API key.
    """
    if not settings.RAG_ENABLED or not texts:
        return []
    key = resolve_embedding_api_key()
    if not key:
        logger.warning("RAG enabled but no OPENAI_API_KEY (or AI_API_KEY with AI_PROVIDER=openai); skipping embed")
        return []

    try:
        from openai import OpenAI

        client = OpenAI(api_key=key)
        out: list[list[float]] = []
        batch = max(1, min(settings.RAG_EMBED_BATCH_SIZE, 100))
        for i in range(0, len(texts), batch):
            chunk = texts[i : i + batch]
            resp = client.embeddings.create(model=settings.RAG_EMBEDDING_MODEL, input=chunk)
            for item in resp.data:
                out.append(list(item.embedding))
        return out
    except Exception as exc:
        logger.warning(f"Embedding batch failed: {exc}")
        return []
