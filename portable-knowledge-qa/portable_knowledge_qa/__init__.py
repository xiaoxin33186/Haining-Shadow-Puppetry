"""Portable knowledge retrieval and grounded question answering."""

from .config import KnowledgeQAConfig
from .engine import KnowledgeQAEngine
from .models import QueryResult, RetrievalResult, SourceRef

__all__ = [
    "KnowledgeQAConfig",
    "KnowledgeQAEngine",
    "QueryResult",
    "RetrievalResult",
    "SourceRef",
]

__version__ = "0.4.0"
