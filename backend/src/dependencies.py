"""Shared FastAPI dependency injection providers.

Central registry for database session, NER service, and RAG service
dependencies.
"""

from typing import Any

from backend.src.db.session import get_db

__all__ = ["get_db", "get_ner_service", "get_rag_service"]


def get_ner_service():
    """Provide singleton NERService instance for request handlers."""
    from backend.src.ner.service import ner_service

    return ner_service


def get_rag_service() -> Any:
    """Provide active RAGService instance for chat and ingest routes."""
    from backend.src.chat.service import get_rag_service as _get_rag_service

    return _get_rag_service()
