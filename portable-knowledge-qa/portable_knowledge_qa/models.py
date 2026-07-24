from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ParsedSection:
    path: str
    title: str
    locator: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QAPair:
    question: str
    answer: str
    path: str
    locator: str
    qa_id: str = ""
    source_text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedDocument:
    path: str
    title: str
    sections: tuple[ParsedSection, ...]
    qa_pairs: tuple[QAPair, ...]
    fingerprint: str
    role: str = "knowledge"


@dataclass(frozen=True)
class KnowledgeChunk:
    chunk_id: str
    text: str
    path: str
    title: str
    locator: str
    kind: str = "knowledge"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceRef:
    path: str
    title: str
    locator: str
    kind: str
    score: float
    excerpt: str
    qa_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RetrievalResult:
    query: str
    match_type: str
    confidence: float
    contexts: tuple[str, ...]
    sources: tuple[SourceRef, ...]
    authoritative_answer: str = ""

    @property
    def has_evidence(self) -> bool:
        return bool(self.contexts)

    def to_dict(
        self,
        include_sources: bool = False,
        include_evidence: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "query": self.query,
            "match_type": self.match_type,
            "confidence": round(self.confidence, 6),
            "context_count": len(self.contexts),
        }
        if include_sources:
            payload["sources"] = [source.to_dict() for source in self.sources]
        if include_evidence:
            payload["evidence"] = list(self.contexts)
            if self.authoritative_answer:
                payload["authoritative_answer"] = self.authoritative_answer
        return payload


@dataclass(frozen=True)
class QueryResult:
    query: str
    answer: str
    intent: str
    styled_text: str
    match_type: str
    confidence: float
    source_count: int
    sources: tuple[SourceRef, ...] = ()
    generation_mode: str = "extractive"
    grounded: bool = True
    generation_status: str = "extractive"
    error: str = ""

    def to_dict(self, include_sources: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "query": self.query,
            "answer": self.answer,
            "intent": self.intent,
            "styled_text": self.styled_text,
            "match_type": self.match_type,
            "confidence": round(self.confidence, 6),
            "source_count": self.source_count,
            "generation_mode": self.generation_mode,
            "grounded": self.grounded,
            "generation_status": self.generation_status,
        }
        if self.error:
            payload["error"] = self.error
        if include_sources:
            payload["sources"] = [source.to_dict() for source in self.sources]
        return payload
