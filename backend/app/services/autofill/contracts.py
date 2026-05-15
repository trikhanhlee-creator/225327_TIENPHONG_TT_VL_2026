from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CanonicalFormField:
    """Canonical field representation shared by all autofill agents."""
    field_key: str
    label: str
    field_type: str = "text"
    required: bool = False
    group: str = "general"
    aliases: list[str] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    raw_context: dict[str, Any] = field(default_factory=dict)


@dataclass
class CanonicalFormSchema:
    """Normalized form schema produced by parse + understanding agents."""
    source_type: str
    source_ref: str
    filename: str
    fields: list[CanonicalFormField]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryCandidate:
    """One memory candidate retrieved for a target field."""
    field_key: str
    value: str
    memory_type: str
    score: float
    confidence: float
    source_ref: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AutofillDecision:
    """Agent output for one field."""
    field_key: str
    value: str
    confidence: float
    reason: str
    source_trace: list[dict[str, Any]] = field(default_factory=list)
    fallback_used: bool = False

