"""Unit tests for RAG chunk utilities and merge behavior (no DB required)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from app.services.autofill.contracts import CanonicalFormField
from app.services.autofill.memory_chunk_indexer import (
    MemoryChunkIndexer,
    StandaloneTextIndexer,
    chunk_texts_for_memory_item,
    cosine_similarity,
    split_text_chunks,
)
from app.services.autofill.memory_retrieval_agent import (
    LLMMemoryRetrievalAgent,
    TIER_CONFIRMED,
    TIER_ENTRY,
    TIER_MEMORY,
    TIER_RAG_FIELD,
    _composite_score,
    _extract_rag_value,
    _rank_key,
)
from app.services.autofill.contracts import MemoryCandidate


def test_cosine_similarity_orthogonal():
    assert abs(cosine_similarity([1.0, 0.0], [0.0, 1.0])) < 1e-9
    assert abs(cosine_similarity([1.0, 0.0], [1.0, 0.0]) - 1.0) < 1e-9


def test_split_text_chunks_overlap():
    t = "a" * 100
    parts = split_text_chunks(t, limit=30, overlap=5)
    assert len(parts) >= 3
    merged = "".join(parts)  # overlap means not exact join
    assert len(merged) >= 90


def test_chunk_texts_for_memory_item_prefix():
    item = MagicMock()
    item.field_key = "full_name"
    item.value_text = "Nguyen Van A"
    texts = chunk_texts_for_memory_item(item)
    assert len(texts) == 1
    assert "full_name" in texts[0]
    assert "Nguyen" in texts[0]


@patch("app.services.autofill.memory_retrieval_agent.embed_texts_sync", return_value=[])
def test_retrieval_falls_back_when_no_embedding(mock_embed):
    agent = LLMMemoryRetrievalAgent()
    m = MagicMock()
    f = m.filter.return_value
    f.order_by.return_value.limit.return_value.all.return_value = []
    f.count.return_value = 0
    m.join.return_value.filter.return_value.all.return_value = []
    db = MagicMock()
    db.query.return_value = m

    field = CanonicalFormField(
        field_key="email",
        label="Email",
        field_type="text",
    )
    out = agent.retrieve_for_field(db=db, user_id=1, field=field, top_k=3)
    assert out == []
    mock_embed.assert_called()


def test_semantic_search_scores_chunks():
    q = [1.0, 0.0, 0.0]
    row = MagicMock()
    row.embedding_json = json.dumps([1.0, 0.0, 0.0])
    row.id = 1
    row.chunk_text = "email: a@b.com"
    row.source_ref = None
    row.memory_item_id = 10
    row.field_key = "email"

    db = MagicMock()
    query_mock = MagicMock()
    db.query.return_value = query_mock
    chain = query_mock.filter.return_value.order_by.return_value.limit.return_value
    chain.all.return_value = [row]

    hits = MemoryChunkIndexer.semantic_search_for_user(db, user_id=1, query_vector=q, top_k=5)
    assert len(hits) == 1
    assert hits[0][1] > 0.99


def test_extract_rag_value_from_prefixed_chunk():
    assert _extract_rag_value("email: user@test.com", "email") == "user@test.com"
    assert _extract_rag_value("full_name: Nguyen Van A\nother: x", "full_name") == "Nguyen Van A"


def test_rank_confirmed_beats_rag():
    confirmed = MemoryCandidate(
        field_key="email",
        value="a@b.com",
        memory_type="confirmed",
        score=_composite_score(tier=TIER_CONFIRMED, confidence=0.95, score=1.0, value="a@b.com"),
        confidence=0.95,
        metadata={"tier": TIER_CONFIRMED, "composite_score": _composite_score(tier=TIER_CONFIRMED, confidence=0.95, score=1.0, value="a@b.com")},
    )
    rag = MemoryCandidate(
        field_key="email",
        value="long rag chunk",
        memory_type="rag",
        score=_composite_score(tier=TIER_RAG_FIELD, confidence=0.99, score=5.0, similarity=0.99, value="long rag chunk"),
        confidence=0.99,
        metadata={"tier": TIER_RAG_FIELD, "composite_score": _composite_score(tier=TIER_RAG_FIELD, confidence=0.99, score=5.0, similarity=0.99, value="long rag chunk")},
    )
    ordered = sorted([rag, confirmed], key=_rank_key)
    assert ordered[0].metadata["tier"] == TIER_CONFIRMED


def test_rank_memory_beats_entry():
    memory = MemoryCandidate(
        field_key="name",
        value="A",
        memory_type="profile",
        score=800,
        confidence=0.8,
        metadata={"tier": TIER_MEMORY, "composite_score": 900},
    )
    entry = MemoryCandidate(
        field_key="name",
        value="B",
        memory_type="entry",
        score=400,
        confidence=0.9,
        metadata={"tier": TIER_ENTRY, "composite_score": 500},
    )
    ordered = sorted([entry, memory], key=_rank_key)
    assert ordered[0].metadata["tier"] == TIER_MEMORY


@patch("app.services.autofill.memory_chunk_indexer.embed_texts_sync", return_value=[])
def test_standalone_indexer_returns_zero_without_embed(mock_embed):
    db = MagicMock()
    n = StandaloneTextIndexer.index_plaintext(
        db,
        user_id=1,
        text="hello world profile text",
        source_ref="test:doc",
    )
    assert n == 0
    mock_embed.assert_called()
