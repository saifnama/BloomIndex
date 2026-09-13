"""RAG orchestration service for multi-turn chat and streaming queries.

Composes ingestion, retrieval, and citation mixins with the shared LLM
client adapter and Qdrant storage backends.
"""

import logging
import os
import re
import threading
from typing import Any

from backend.src.common.uploads import (
    delete_user_upload_file,
    delete_user_uploads,
    get_user_markdown_file_path,
)
from backend.src.settings import (
    RAG_CITATION_SUPPORT_FLOOR,
    RAG_CITATION_SUPPORT_MARGIN,
)

logger = logging.getLogger(__name__)

from backend.src.chat.citations import _CitationsMixin
from backend.src.chat.config import (
    LLM_TEMPERATURE,
    RAG_QUERY_TIMEOUT_SECONDS,
    RAG_SUMMARY_TIMEOUT_SECONDS,
)
from backend.src.chat.embeddings import BloomIndexEmbeddings, config, get_optimal_device
from backend.src.chat.ingest import _IngestMixin
from backend.src.chat.llm import RAGLLMTimeoutError, RAGProviderAuthError, SDKLLMAdapter
from backend.src.chat.retrieval import _RetrievalMixin


class RAGService(_IngestMixin, _RetrievalMixin, _CitationsMixin):
    """Chat RAG service orchestrating ingest, retrieval, and citations."""

    _atexit_registered: bool = False

    def __init__(self, llm=None):
        self._device = get_optimal_device()
        self.embeddings = BloomIndexEmbeddings(
            model=config.embedding_model,
            device=self._device,
            mrl_dim=config.embedding_dim,
            query_instruction=config.embedding_instruction,
        )
        # Synchronize service device if embeddings fell back to CPU.
        self._device = self.embeddings.device

        # Defer reranker loading until first invocation.
        self._reranker = ...  # Sentinel: not loaded yet.
        self._reranker_lock = threading.Lock()

        # Shared LLM adapter initialized without blocking I/O.
        self.llm = llm or SDKLLMAdapter(temperature=LLM_TEMPERATURE)

        # Per-user vectorstore client and collection state.
        self._vectorstore_cache: dict[str, Any] = {}
        self._qdrant_client = None
        self._qdrant_lock = threading.Lock()

        # Guard against duplicate atexit handler registrations.
        self._atexit_registered = False

        # Lazy caches for Docling and semantic splitters.
        self._docling_converter = None
        self._semantic_splitter = None

    async def _invoke_llm(
        self,
        *,
        prompt: str = None,
        messages: list = None,
        timeout_seconds: float | None = None,
        max_retries: int = 3,
        response_format: dict[str, Any] | None = None,
    ):
        # Progressively drop optional keyword arguments if an underlying
        # adapter or test mock rejects specific parameters.
        attempts = [
            dict(
                prompt=prompt,
                messages=messages,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
                response_format=response_format,
            ),
            dict(
                prompt=prompt,
                messages=messages,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
            ),
            dict(
                prompt=prompt,
                messages=messages,
                max_retries=max_retries,
            ),
            dict(prompt=prompt, messages=messages),
        ]
        last_type_error: TypeError | None = None
        for kwargs in attempts:
            try:
                return await self.llm.invoke(**kwargs)
            except TypeError as exc:
                if "unexpected keyword argument" not in str(exc):
                    raise
                last_type_error = exc
                continue
        # Re-raise the final TypeError if all argument profiles failed.
        raise last_type_error  # type: ignore[misc]

    KB_COLLECTION_NAME = "kb_papers"

    _kb_vectorstore_cache: Any | None = None

    _KB_DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"

    async def query(
        self,
        question: str,
        filter_files: list[str] | None = None,
        user_id: str = "default",
        chat_history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Execute a non-streaming RAG query and return the full answer."""
        prepared = await self._prepare_query(question, filter_files, user_id, chat_history)
        if "answer" in prepared:
            return prepared

        response = await self._invoke_llm(
            messages=prepared["messages"], timeout_seconds=RAG_QUERY_TIMEOUT_SECONDS
        )
        answer_text = (response.content or "").strip()

        if answer_text and not prepared.get("is_kb_mode"):
            # Parse inline [cN] citation markers into reference records.
            try:
                citable = prepared.get("citable_sources", prepared.get("sources", []))
                valid_ids = {s["chunk_id"] for s in citable if s.get("chunk_id")}
                nums = re.findall(r"\[\s*[Cc]?\s*(\d+)\s*\]", answer_text)
                used_ids = []
                seen: set = set()
                for n in nums:
                    cid = f"c{n}"
                    if cid in valid_ids and cid not in seen:
                        seen.add(cid)
                        used_ids.append(cid)
                # Clean visible text without inline citation markers.
                cleaned = re.sub(r"\[†\]", "", answer_text)
                cleaned = re.sub(r"\[\s*[Cc]?\s*\d+\s*\]", "", cleaned)
                cleaned = re.sub(r"  +", " ", cleaned)
                answer_text = cleaned
                # Fall back to all citable sources if no markers exist.
                ref_ids = (
                    used_ids if used_ids else [s["chunk_id"] for s in citable if s.get("chunk_id")]
                )
                if ref_ids:
                    pseudo = [
                        {
                            "chunk_id": cid,
                            "page": next(
                                (s.get("page") for s in citable if s.get("chunk_id") == cid), None
                            ),
                            "title": next(
                                (
                                    s.get("doc_title") or s.get("title") or ""
                                    for s in citable
                                    if s.get("chunk_id") == cid
                                ),
                                "",
                            ),
                            "source": next(
                                (
                                    s.get("source") or ""
                                    for s in citable
                                    if s.get("chunk_id") == cid
                                ),
                                "",
                            ),
                        }
                        for cid in ref_ids
                    ]
                    refs = self._build_references_block(pseudo, citable)
                    if refs and "**References**" not in answer_text:
                        answer_text = answer_text + refs
            except Exception as e:
                logger.warning(f"Fallback citation build failed: {e}")

        if prepared.get("is_kb_mode") and answer_text:
            seen_papers: dict[str, dict[str, str]] = {}
            for s in prepared.get("citable_sources", prepared.get("sources", [])):
                key = s.get("source", "")
                if key and key not in seen_papers:
                    seen_papers[key] = {
                        "title": s.get("doc_title", ""),
                        "doi": s.get("doc_doi", ""),
                    }
            if seen_papers:
                ref_lines = ["\n\n---\n\n**References**\n"]
                for idx, (fname, meta) in enumerate(seen_papers.items(), 1):
                    title = meta["title"] or fname
                    doi = meta["doi"]
                    line = f"{idx}. {title}"
                    if doi:
                        line += f". DOI: {doi}"
                    ref_lines.append(line)
                answer_text += "\n" + "\n".join(ref_lines)

        return {
            "answer": answer_text,
            "sources": prepared["sources"],
        }

    async def query_stream(
        self,
        question: str,
        filter_files: list[str] | None = None,
        user_id: str = "default",
        chat_history: list[dict[str, str]] | None = None,
    ):
        """Execute a streaming RAG query yielding NDJSON-compatible frames.

        Emits text_delta, sources, error, and done frames matching the
        frontend StreamFrame protocol.
        """
        try:
            prepared = await self._prepare_query(question, filter_files, user_id, chat_history)
        except Exception as e:
            yield {"type": "error", "error": str(e)}
            return

        # Short-circuit on empty retrieval context.
        if "answer" in prepared:
            yield {"type": "text_delta", "text": prepared["answer"]}
            yield {"type": "sources", "sources": prepared["sources"]}
            yield {"type": "done"}
            return

        # Emit sources frame before streaming tokens so the frontend
        # can register valid citation targets in advance.
        yield {"type": "sources", "sources": prepared["sources"]}

        # Stream LLM tokens while accumulating the complete text.
        accumulated = ""
        try:
            async for chunk in self.llm.astream(messages=prepared["messages"]):
                if chunk:
                    accumulated += chunk
                    yield {"type": "text_delta", "text": chunk}
        except RAGProviderAuthError as e:
            yield {"type": "error", "error": f"Auth failed: {e}"}
            return
        except Exception as e:
            logger.exception("query_stream LLM call failed")
            yield {"type": "error", "error": f"LLM stream failed: {e}"}
            return

        # Retain only sources presented in context for attribution.
        citable_sources = prepared.get("citable_sources", prepared.get("sources", []))

        # Log citation diagnostics for evaluation and auditing.
        if accumulated.strip():
            retrieved_id_set = {s["chunk_id"] for s in citable_sources}
            # Permissive marker regex matching [c1], [1], [C1], etc.
            raw_markers = re.findall(r"\[\s*[Cc]?\s*(\d+)\s*\]", accumulated)
            found_markers = [f"c{num}" for num in raw_markers if f"c{num}" in retrieved_id_set]
            unique_cited = sorted(set(found_markers))
            retrieved_ids = sorted(s["chunk_id"] for s in citable_sources)
            # Build per-chunk diagnostic mapping and validate character
            # offsets against the saved paper markdown.
            sources_for_log = citable_sources
            md_cache: dict[str, str | None] = {}

            def _load_md_once(filename: str) -> str | None:
                if filename in md_cache:
                    return md_cache[filename]
                try:
                    md_path = get_user_markdown_file_path(user_id, filename)
                    if md_path.is_file():
                        md_cache[filename] = md_path.read_text(encoding="utf-8")
                    else:
                        md_cache[filename] = None
                except Exception:
                    md_cache[filename] = None
                return md_cache[filename]

            def _validate(s: dict[str, Any]) -> str:
                bs = s.get("body_start")
                be = s.get("body_end")
                if bs is None or be is None:
                    return "no-offset"
                md = _load_md_once(s.get("source", ""))
                if md is None:
                    return f"off{bs}-md-missing"
                if not (0 <= bs < be <= len(md)):
                    return f"off{bs}-OOB(md_len={len(md)})"
                slice_text = md[bs:be]
                ctext = s.get("chunk_text", "")
                if slice_text == ctext:
                    return f"off{bs}-OK"
                # Identify first differing offset for error diagnosis.
                limit = min(len(slice_text), len(ctext))
                first_diff = next(
                    (i for i in range(limit) if slice_text[i] != ctext[i]),
                    limit,
                )
                return (
                    f"off{bs}-MISMATCH("
                    f"slice_len={len(slice_text)},"
                    f"ctext_len={len(ctext)},"
                    f"first_diff={first_diff})"
                )

            id_to_source = {
                s["chunk_id"]: (
                    f"{s.get('source', '?')}"
                    + (f":p{s['page']}" if s.get("page") else "")
                    + f":{_validate(s)}"
                )
                for s in sources_for_log
            }
            # Sample initial output tokens to diagnose compliance.
            snippet_raw = accumulated[:280]
            snippet = re.sub(r"\s+", " ", snippet_raw).strip()
            # Capture bracketed substrings to detect non-standard tags.
            any_brackets = re.findall(r"\[[^\]\n]{1,40}\]", snippet_raw)[:8]
            logger.warning(
                "[CITATION DIAG] total_markers=%d unique_cited=%d/%d "
                "retrieved=%s cited=%s mapping=%s "
                "any_brackets=%s snippet=%r",
                len(found_markers),
                len(unique_cited),
                len(retrieved_ids),
                retrieved_ids,
                unique_cited,
                id_to_source,
                any_brackets,
                snippet,
            )

        is_kb_mode = prepared.get("is_kb_mode", False)

        if is_kb_mode:
            corrected_answer = accumulated
            citations: list[dict[str, Any]] = []
            if accumulated.strip():
                seen_papers: dict[str, dict[str, str]] = {}
                for s in citable_sources:
                    key = s.get("source", "")
                    if key and key not in seen_papers:
                        seen_papers[key] = {
                            "title": s.get("doc_title", ""),
                            "doi": s.get("doc_doi", ""),
                        }
                if seen_papers:
                    ref_lines = ["\n\n---\n\n**References**\n"]
                    for idx, (fname, meta) in enumerate(seen_papers.items(), 1):
                        title = meta["title"] or fname
                        doi = meta["doi"]
                        line = f"{idx}. {title}"
                        if doi:
                            line += f". DOI: {doi}"
                        ref_lines.append(line)
                    corrected_answer = accumulated + "\n" + "\n".join(ref_lines)
        else:
            # Parse [cN] citations and strip inline markers from view.
            citations: list[dict[str, Any]] = []
            rewrite_error: str | None = None
            attribution_mode = "llm_inline"
            valid_ids = {s["chunk_id"] for s in citable_sources if s.get("chunk_id")}
            nums = re.findall(r"\[\s*[Cc]?\s*(\d+)\s*\]", accumulated)
            used_ids: list[str] = []
            seen: set = set()
            for n in nums:
                cid = f"c{n}"
                if cid in valid_ids and cid not in seen:
                    seen.add(cid)
                    used_ids.append(cid)
            # Strip inline markers and daggers from visible text.
            cleaned = re.sub(r"\[†\]", "", accumulated)
            cleaned = re.sub(r"\[\s*[Cc]?\s*\d+\s*\]", "", cleaned)
            cleaned = re.sub(r"  +", " ", cleaned)
            corrected_answer = cleaned
            # Fall back to sentence attribution if the model omitted
            # citation markers.
            fallback_used = False
            if not used_ids and accumulated.strip():
                try:
                    _, diag_cites = self._attribute_sentences_to_sources(
                        accumulated,
                        citable_sources,
                        floor=RAG_CITATION_SUPPORT_FLOOR,
                        margin=RAG_CITATION_SUPPORT_MARGIN,
                    )
                    for c in diag_cites:
                        cid = c.get("chunk_id")
                        if cid in valid_ids and cid not in seen:
                            seen.add(cid)
                            used_ids.append(cid)
                    citations = diag_cites
                    fallback_used = True
                except Exception as e:
                    logger.warning(f"Fallback attribution failed: {e}")
                    citations = []

            if accumulated.strip():
                citations_summary = [
                    {
                        "chunk_id": cid,
                        "quote_preview": next(
                            (c.get("quote") or "")[:40]
                            for c in citations
                            if c.get("chunk_id") == cid
                        )
                        if citations
                        else "",
                    }
                    for cid in used_ids
                ]
                logger.warning(
                    "[CITATION DIAG] mode=%s omitted=%d parsed=%s fallback=%s citations=%s%s",
                    attribution_mode,
                    len(prepared.get("sources", [])) - len(citable_sources),
                    used_ids,
                    fallback_used,
                    citations_summary,
                    f" error={rewrite_error!r}" if rewrite_error else "",
                )

        # Build appended References block from cited chunks.
        if not is_kb_mode and corrected_answer.strip():
            ref_ids = (
                used_ids
                if used_ids
                else [s["chunk_id"] for s in citable_sources if s.get("chunk_id")]
            )
            pseudo = [
                {
                    "chunk_id": cid,
                    "page": next(
                        (s.get("page") for s in citable_sources if s.get("chunk_id") == cid), None
                    ),
                    "title": next(
                        (
                            s.get("doc_title") or s.get("title") or ""
                            for s in citable_sources
                            if s.get("chunk_id") == cid
                        ),
                        "",
                    ),
                    "source": next(
                        (
                            s.get("source") or ""
                            for s in citable_sources
                            if s.get("chunk_id") == cid
                        ),
                        "",
                    ),
                }
                for cid in ref_ids
            ]
            refs = self._build_references_block(pseudo, citable_sources)
            if refs:
                if "**References**" not in corrected_answer:
                    corrected_answer = corrected_answer + refs

        if corrected_answer != accumulated:
            yield {"type": "answer_corrected", "text": corrected_answer}

        yield {"type": "citations", "citations": citations}

        yield {"type": "done"}

    async def suggest_followups(
        self,
        chat_history: list[dict[str, str]],
        max_suggestions: int = 3,
    ) -> list[str]:
        """Generate short follow-up prompts based on conversation context.

        Returns an empty list on failure so the UI can gracefully treat
        suggestions as optional hints.
        """
        if not chat_history:
            return []

        # Restrict context window to the last 3 conversation turns.
        window = chat_history[-6:]
        transcript_lines = []
        for m in window:
            role = m.get("role", "user")
            content = (m.get("content") or "").strip()
            if not content:
                continue
            label = "User" if role == "user" else "Assistant"
            transcript_lines.append(f"{label}: {content[:600]}")
        transcript = "\n".join(transcript_lines)
        if not transcript:
            return []

        prompt = (
            "Given the conversation below between a researcher and an "
            "assistant about scientific papers, propose "
            f"{max_suggestions} short follow-up questions the user "
            "might ask next. Each question must:\n"
            "- be self-contained and clearly worded\n"
            "- be 12 words or fewer\n"
            "- not repeat anything already asked\n\n"
            f"Conversation:\n{transcript}\n\n"
            "Return ONLY the questions, one per line, with no "
            "numbering, bullets, or extra prose."
        )

        try:
            response = await self._invoke_llm(
                prompt=prompt,
                max_retries=1,
                timeout_seconds=15.0,
            )
        except Exception as e:
            logger.warning(f"suggest_followups LLM call failed: {e}")
            return []

        raw = (response.content or "").strip()
        if not raw:
            return []

        # Split lines and remove leading enumeration or bullet prefixes.
        lines = []
        for line in raw.splitlines():
            cleaned = line.strip()
            if not cleaned:
                continue
            cleaned = re.sub(r"^[\-\*•]\s*", "", cleaned)
            cleaned = re.sub(r"^\d+[\.\)]\s*", "", cleaned)
            if not cleaned:
                continue
            # Filter out non-question headings or introductory preamble.
            if len(cleaned) < 6:
                continue
            lines.append(cleaned)
            if len(lines) >= max_suggestions:
                break
        return lines

    async def summarize_document(self, text: str, filename: str) -> str:
        """Generate a concise 2-3 sentence summary of a document."""
        try:
            # Limit input excerpt to stay within token reserves.
            excerpt = text[:2000]
            prompt = f"""Summarize this research paper excerpt in exactly 2-3 sentences. Focus on the main topic, methods, and key findings.

Excerpt:
{excerpt}

Summary:"""
            response = await self._invoke_llm(
                prompt=prompt, max_retries=1, timeout_seconds=RAG_SUMMARY_TIMEOUT_SECONDS
            )
            return response.content.strip()
        except RAGLLMTimeoutError as e:
            logger.warning(f"Summary generation timed out for {filename}: {e}")
            return ""
        except Exception as e:
            logger.warning(f"Summarization failed for {filename}: {e}")
            return ""

    def list_indexed_files(self, user_id: str = "default") -> list[dict[str, Any]]:
        """Query Qdrant for all unique indexed source files for a user.

        Scrolls the user collection in batches and aggregates metadata
        keyed by filename. Returns an empty list if the collection does
        not yet exist.
        """
        try:
            client = self._get_qdrant_client()
            collection_name = self._get_user_collection_name(user_id)

            # Probe existence; treat 404/not found as uninitialized.
            try:
                client.get_collection(collection_name)
            except Exception as exc:
                msg = str(exc).lower()
                if (
                    "not found" in msg
                    or "404" in msg
                    or "doesn't exist" in msg
                    or "does not exist" in msg
                ):
                    return []
                # Attempt scroll regardless if the error is non-fatal.
                logger.warning(
                    f"get_collection probe raised non-404 for {collection_name}: {exc!r}; attempting scroll anyway"
                )

            file_map: dict[str, dict[str, Any]] = {}
            offset = None
            while True:
                points, offset = client.scroll(
                    collection_name=collection_name,
                    limit=1000,
                    with_payload=True,
                    with_vectors=False,
                    offset=offset,
                )
                for point in points:
                    payload = point.payload or {}
                    meta = payload.get("metadata", {}) or {}
                    src = meta.get("source", "")
                    if not src:
                        continue
                    if src not in file_map:
                        file_map[src] = {
                            "name": src,
                            "file_type": meta.get("file_type", os.path.splitext(src)[1] or ".pdf"),
                            "chunk_count": 0,
                            "indexed_at": meta.get("indexed_at", ""),
                            "parser_type": meta.get("parser_type", "docling"),
                            "authors": meta.get("doc_authors", ""),
                            "doi": meta.get("doc_doi", ""),
                            "journal": meta.get("doc_journal", ""),
                        }
                    file_map[src]["chunk_count"] += 1
                if offset is None:
                    break

            return list(file_map.values())

        except Exception as e:
            logger.error(f"Error listing indexed files: {e}")
            return []

    def delete_source(self, filename: str, user_id: str = "default") -> bool:
        """Delete an indexed source, its chunks, and associated file uploads."""
        from qdrant_client.http import models as qmodels

        try:
            self._get_user_collection(user_id)  # ensure collection exists
            client = self._get_qdrant_client()
            collection_name = self._get_user_collection_name(user_id)

            client.delete(
                collection_name=collection_name,
                points_selector=qmodels.FilterSelector(
                    filter=qmodels.Filter(
                        must=[
                            qmodels.FieldCondition(
                                key="metadata.source",
                                match=qmodels.MatchValue(value=filename),
                            )
                        ]
                    )
                ),
            )
            logger.info(f"Deleted chunks for '{filename}' from user {user_id}'s Qdrant collection")

            self._cleanup_parent_store(user_id)
            delete_user_upload_file(user_id, filename)
            self._invalidate_user_collection(user_id)
            return True
        except Exception as e:
            self._invalidate_user_collection(user_id)
            logger.error(f"Error deleting source '{filename}': {e}")
            return False

    def reset_rag(self, user_id: str = "default") -> bool:
        """Permanently delete all indexed data and uploads for a user."""
        try:
            client = self._get_qdrant_client()
            collection_name = self._get_user_collection_name(user_id)
            try:
                client.delete_collection(collection_name=collection_name)
                logger.info(f"Deleted Qdrant collection {collection_name} for user {user_id}")
            except Exception as exc:
                # Collection may not exist yet if no files were indexed.
                logger.info(f"Qdrant collection delete for {user_id} skipped: {exc!r}")

            parent_path = self._get_parent_store_path(user_id)
            if os.path.exists(parent_path):
                os.remove(parent_path)
                logger.info(f"Deleted parent store for user {user_id}")

            delete_user_uploads(user_id)
            self._invalidate_user_collection(user_id)
            return True
        except Exception as e:
            self._invalidate_user_collection(user_id)
            logger.error(f"Error resetting RAG data for user {user_id}: {e}")
            return False

    def cleanup_user(self, user_id: str) -> bool:
        """Clean up user storage and collections upon session termination."""
        success = True

        try:
            self.reset_rag(user_id)
        except Exception as e:
            logger.warning(f"Could not reset user data cleanly: {e}")
            success = False

        self._invalidate_user_collection(user_id)

        # Ensure the parent store is deleted if reset_rag was bypassed.
        parent_path = self._get_parent_store_path(user_id)
        try:
            if os.path.exists(parent_path):
                os.remove(parent_path)
                logger.info(f"Deleted parent store: {parent_path}")
        except Exception as e:
            logger.warning(f"Could not delete parent store: {e}")

        try:
            delete_user_uploads(user_id)
        except Exception as e:
            logger.error(f"Error deleting uploads folder: {e}")
            success = False

        if success:
            logger.info(f"Cleaned up all data for user: {user_id}")
        return success


_rag_service: RAGService | None = None


def get_rag_service() -> RAGService:
    global _rag_service
    if _rag_service is None:
        _rag_service = RAGService()
    return _rag_service


def peek_rag_service() -> RAGService | None:
    return _rag_service
