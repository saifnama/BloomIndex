"""Runtime configuration and tuning defaults for the chat RAG pipeline.

Defines operational thresholds, request timeouts, and candidate
retrieval batch sizes tuned to balance model inference latency against
reranking precision.
"""

import logging

from backend.src.settings import RAG_TEMPERATURE, _safe_float, _safe_int

logger = logging.getLogger(__name__)

LLM_TEMPERATURE = RAG_TEMPERATURE


RAG_QUERY_TIMEOUT_SECONDS = _safe_float("RAG_QUERY_TIMEOUT_SECONDS", 45.0)


RAG_SUMMARY_TIMEOUT_SECONDS = _safe_float("RAG_SUMMARY_TIMEOUT_SECONDS", 20.0)


RAG_RERANK_CANDIDATE_K = _safe_int("RAG_RERANK_CANDIDATE_K", 40)


RAG_RERANK_BATCH_SIZE = _safe_int("RAG_RERANK_BATCH_SIZE", 8)
