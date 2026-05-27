from __future__ import annotations

from app.core.config import settings
from app.core.logger import logger


def resolve_embedding_api_key() -> str:
    if settings.OPENAI_API_KEY.strip():
        return settings.OPENAI_API_KEY.strip()
    if settings.AI_PROVIDER.strip().lower() == "openai" and settings.AI_API_KEY.strip():
        return settings.AI_API_KEY.strip()
    return ""


def resolve_gemini_embedding_api_key() -> str:
    gemini_key = (getattr(settings, "GEMINI_API_KEY", "") or "").strip()
    if gemini_key:
        return gemini_key
    if settings.AI_PROVIDER.strip().lower() == "gemini" and settings.AI_API_KEY.strip():
        return settings.AI_API_KEY.strip()
    return ""


def _gemini_embedding_model() -> str:
    model = (settings.RAG_EMBEDDING_MODEL or "").strip()
    if model and ("embedding" in model.lower() or model.startswith("models/")):
        return model if model.startswith("models/") else f"models/{model}"
    return "models/gemini-embedding-001"


def _embed_with_openai(texts: list[str]) -> list[list[float]]:
    key = resolve_embedding_api_key()
    if not key:
        return []

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


def _embed_with_gemini(texts: list[str]) -> list[list[float]]:
    key = resolve_gemini_embedding_api_key()
    if not key:
        return []

    import google.generativeai as genai

    genai.configure(api_key=key)
    model = _gemini_embedding_model()
    out: list[list[float]] = []
    for text in texts:
        if not (text or "").strip():
            out.append([])
            continue
        result = genai.embed_content(
            model=model,
            content=text,
            task_type="retrieval_document",
        )
        embedding = result.get("embedding") if isinstance(result, dict) else getattr(result, "embedding", None)
        out.append(list(embedding or []))
    return out


def embed_texts_sync(texts: list[str]) -> list[list[float]]:
    """
    Batch embedding for RAG. Tries OpenAI first, then Gemini when OpenAI is unavailable.
    """
    if not settings.RAG_ENABLED or not texts:
        return []

    provider = (getattr(settings, "RAG_EMBEDDING_PROVIDER", "") or "").strip().lower()
    prefer_gemini = provider == "gemini" or settings.AI_PROVIDER.strip().lower() == "gemini"

    if prefer_gemini:
        gemini_vecs = _embed_with_gemini(texts)
        if gemini_vecs and any(gemini_vecs):
            return gemini_vecs
        logger.warning("Gemini embedding failed or empty; trying OpenAI fallback for RAG")

    if resolve_embedding_api_key():
        try:
            openai_vecs = _embed_with_openai(texts)
            if openai_vecs:
                return openai_vecs
        except Exception as exc:
            logger.warning(f"OpenAI embedding batch failed: {exc}")

    gemini_vecs = _embed_with_gemini(texts)
    if gemini_vecs and any(gemini_vecs):
        return gemini_vecs

    if not resolve_embedding_api_key() and not resolve_gemini_embedding_api_key():
        logger.warning(
            "RAG enabled but no embedding API key (OPENAI_API_KEY or GEMINI_API_KEY / AI_API_KEY with gemini)"
        )
    return []
