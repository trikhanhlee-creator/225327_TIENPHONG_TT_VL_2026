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
from app.services.autofill.memory_retrieval_agent import LLMMemoryRetrievalAgent


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
