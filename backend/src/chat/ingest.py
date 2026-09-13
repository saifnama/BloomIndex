"""Document ingestion, chunking, and vector indexing pipeline mixin.

Handles multi-stage parsing (PyMuPDF, Docling), parent-child chunking,
dense and sparse vector generation, collection lifecycle, and Qdrant
indexing for the RAG service.
"""

import gc
import hashlib
import json
import logging
import os
import re
import tempfile
import time
from datetime import UTC, datetime
from typing import Any

from backend.src.common.uploads import (
    extract_paper_markdown,
    get_user_markdown_file_path,
)
from backend.src.settings import (
    QDRANT_API_KEY,
    QDRANT_URL,
)

logger = logging.getLogger(__name__)

from backend.src.chat.embeddings import BloomIndexEmbeddings, config
from backend.src.chat.retrieval import _check_bm25


class _IngestMixin:
    def _get_semantic_splitter(self):
        """Lazy-init SemanticChunker for child-level semantic splitting."""
        if getattr(self, "_semantic_splitter", None) is None:
            from langchain_experimental.text_splitter import SemanticChunker

            logger.info("Initializing SemanticChunker for child splitting...")
            self._semantic_splitter = SemanticChunker(
                self.embeddings,
                breakpoint_threshold_type="standard_deviation",
                breakpoint_threshold_amount=1.5,
            )
        return self._semantic_splitter

    def _split_semantic_children(self, text: str) -> list[str]:
        """Split parent text into semantically coherent child chunks.

        Uses SemanticChunker to group sentences by meaning, then applies
        size guards to ensure chunks stay within configured bounds.
        """
        try:
            splitter = self._get_semantic_splitter()
            raw_chunks = splitter.split_text(text)
        except Exception as e:
            from langchain_text_splitters import MarkdownTextSplitter

            logger.warning(f"SemanticChunker failed, falling back to MarkdownTextSplitter: {e}")
            fallback = MarkdownTextSplitter(
                chunk_size=config.child_chunk_size,
                chunk_overlap=config.child_chunk_overlap,
            )
            return fallback.split_text(text)

        result: list[str] = []
        for chunk in raw_chunks:
            if len(chunk) < config.min_chunk_size:
                continue
            if len(chunk) > config.child_chunk_size * 2:
                # Oversized chunk; fall back to character split.
                from langchain_text_splitters import RecursiveCharacterTextSplitter

                safety = RecursiveCharacterTextSplitter(
                    chunk_size=config.child_chunk_size,
                    chunk_overlap=config.child_chunk_overlap,
                    separators=["\n\n", "\n", ". ", " ", ""],
                )
                result.extend(safety.split_text(chunk))
            else:
                result.append(chunk)
        return result

    def _get_collection_suffix(self) -> str:
        """Generate an 8-character hash from model name and dimension.

        Versions Qdrant collections by embedding configuration to avoid
        dimension mismatches when switching models. Loads the model if
        uninitialized to ensure dimension stability across call sites.
        """
        self.embeddings._ensure_model_loaded()
        model_key = f"{self.embeddings.model_name}:{self.embeddings.model_dim}"
        return hashlib.md5(model_key.encode()).hexdigest()[:8]

    def _get_qdrant_client(self):
        """Initialize or return the shared QdrantClient instance.

        Connects to a remote Qdrant server when QDRANT_URL is set, or
        falls back to embedded local storage in config.qdrant_dir.
        Registers an atexit hook on first instantiation to ensure
        connections and storage locks close cleanly on exit.
        """
        if self._qdrant_client is not None:
            return self._qdrant_client
        with self._qdrant_lock:
            if self._qdrant_client is not None:
                return self._qdrant_client
            from qdrant_client import QdrantClient

            # Select connection mode based on QDRANT_URL:
            # - Remote server: supports concurrent clients and workers.
            # - Embedded: single-process storage in config.qdrant_dir.
            if QDRANT_URL:
                self._qdrant_client = QdrantClient(
                    url=QDRANT_URL,
                    api_key=QDRANT_API_KEY or None,
                )
                _auth_note = " (authenticated)" if QDRANT_API_KEY else ""
                logger.info(f"Initialized Qdrant remote client at {QDRANT_URL}{_auth_note}")
            else:
                os.makedirs(config.qdrant_dir, exist_ok=True)
                self._qdrant_client = QdrantClient(path=config.qdrant_dir)
                logger.info(f"Initialized Qdrant local client at {config.qdrant_dir}")

            # Register process-exit cleanup handler once per instance.
            if not self._atexit_registered:
                import atexit

                atexit.register(self._atexit_close)
                self._atexit_registered = True
            return self._qdrant_client

    def _atexit_close(self) -> None:
        """Process exit wrapper for close(); silently swallows exceptions."""
        try:
            self.close()
        except Exception:
            pass

    def close(self) -> None:
        """Close Qdrant client cleanly during application shutdown.

        Idempotent and safe to invoke multiple times. Suppresses errors
        to prevent blocking process shutdown sequences.
        """
        with self._qdrant_lock:
            client = self._qdrant_client
            if client is not None:
                if hasattr(client, "close"):
                    try:
                        client.close()
                        logger.info("Closed Qdrant local client cleanly")
                    except Exception as e:
                        logger.warning(f"Qdrant client close raised (ignored): {e}")
                self._qdrant_client = None
                self._vectorstore_cache.clear()

        self._close_fastembed_models()

    def _close_fastembed_models(self) -> None:
        """Clean up fastembed models and terminate their loky process pool.



        We explicitly terminate and release the executor here so the
        semaphore is unlinked before the resource_tracker runs.

        Idempotent: safe to call when the models were never loaded.
        Best-effort: any exception is silently caught.
        """
        try:
            from joblib.externals.loky import reusable_executor as _loky_re

            executor = _loky_re._executor
            if executor is not None:
                executor.shutdown(wait=True)
                _loky_re._executor = None
                try:
                    _loky_re._executor_kwargs = None
                except AttributeError:
                    pass
        except Exception:
            pass

        try:
            if hasattr(self, "_sparse_embeddings_cache"):
                del self._sparse_embeddings_cache
        except Exception:
            pass

        gc.collect()

    def _ensure_qdrant_collection(self, client, collection_name: str) -> None:
        """Create the per-user Qdrant collection if it does not exist.

        Configures dense vectors using cosine distance and sparse
        vectors using Modifier.IDF for server-side hybrid retrieval.
        """
        from qdrant_client.http import models as qmodels

        try:
            client.get_collection(collection_name)
            return
        except Exception:
            pass  # Collection does not exist; proceed to create.

        # Ensure embedding model is loaded so model_dim is initialized.
        self.embeddings._ensure_model_loaded()
        model_dim = self.embeddings.model_dim
        requested_dim = config.embedding_dim
        # Clamp requested dimension to loaded model capacity if needed.
        if requested_dim and requested_dim > model_dim:
            logger.warning(
                f"RAG_EMBEDDING_DIM={requested_dim} exceeds native output "
                f"dim {model_dim} ({self.embeddings.model_name!r}); "
                f"clamping vector dimension to {model_dim}."
            )
            vec_dim = model_dim
        else:
            vec_dim = requested_dim or model_dim

        client.create_collection(
            collection_name=collection_name,
            vectors_config={
                "dense": qmodels.VectorParams(
                    size=vec_dim,
                    distance=qmodels.Distance.COSINE,
                ),
            },
            sparse_vectors_config={
                "sparse": qmodels.SparseVectorParams(
                    modifier=qmodels.Modifier.IDF,
                ),
            },
        )
        logger.info(
            f"Created Qdrant collection {collection_name} (HYBRID: "
            f"dense size={vec_dim}, sparse IDF-BM25, distance=COSINE)"
        )

    def _get_user_collection(self, user_id: str):
        """Get or create a Qdrant-backed VectorStore for a user.

        Wraps a user Qdrant collection in QdrantVectorStore to support
        document addition and similarity search. The collection name
        encodes the model hash to isolate embeddings versions.
        """
        from langchain_qdrant import QdrantVectorStore, RetrievalMode

        if user_id in self._vectorstore_cache:
            return self._vectorstore_cache[user_id]

        safe_user_id = re.sub(r"[^a-zA-Z0-9_]", "_", user_id)
        model_suffix = self._get_collection_suffix()
        collection_name = f"user_{safe_user_id}_{model_suffix}"

        client = self._get_qdrant_client()
        self._ensure_qdrant_collection(client, collection_name)

        sparse = self._get_sparse_embeddings()
        if sparse is not None:
            mode = RetrievalMode.HYBRID
            mode_label = "HYBRID"
        else:
            mode = RetrievalMode.DENSE
            mode_label = "DENSE (BM25 unavailable)"

        vectorstore = QdrantVectorStore(
            client=client,
            collection_name=collection_name,
            embedding=self.embeddings,
            sparse_embedding=sparse,
            retrieval_mode=mode,
            vector_name="dense",
            sparse_vector_name="sparse",
        )
        logger.info(
            f"Created Qdrant {mode_label} vectorstore for user: {user_id} "
            f"(collection={collection_name})"
        )
        self._vectorstore_cache[user_id] = vectorstore
        return vectorstore

    def _get_kb_collection(self):
        """Return a Qdrant-backed VectorStore for the knowledge base.

        Targets the fixed KB collection created by scripts/ingest_kb.py.
        Returns None when the collection does not yet exist. Reads the
        embedding model configuration from kb.sqlite to ensure parity
        with the offline ingest process.
        """
        from langchain_qdrant import QdrantVectorStore, RetrievalMode

        if type(self)._kb_vectorstore_cache is not None:
            return type(self)._kb_vectorstore_cache

        client = self._get_qdrant_client()
        try:
            client.get_collection(self.KB_COLLECTION_NAME)
        except Exception:
            return None

        kb_model = self._read_kb_config("embedding_model") or self._KB_DEFAULT_EMBEDDING_MODEL
        kb_embeddings = BloomIndexEmbeddings(model=kb_model)

        sparse = self._get_sparse_embeddings()
        if sparse is not None:
            mode = RetrievalMode.HYBRID
            mode_label = "HYBRID"
        else:
            mode = RetrievalMode.DENSE
            mode_label = "DENSE (BM25 unavailable)"

        vectorstore = QdrantVectorStore(
            client=client,
            collection_name=self.KB_COLLECTION_NAME,
            embedding=kb_embeddings,
            sparse_embedding=sparse,
            retrieval_mode=mode,
            vector_name="dense",
            sparse_vector_name="sparse",
            content_payload_key="page_content",
            metadata_payload_key="metadata",
        )
        logger.info(
            f"Created Qdrant {mode_label} vectorstore for KB "
            f"(collection={self.KB_COLLECTION_NAME}, "
            f"embedding={kb_model})"
        )
        type(self)._kb_vectorstore_cache = vectorstore
        return vectorstore

    def _read_kb_config(self, key: str) -> str | None:
        """Read a value from the config table in kb.sqlite.

        Returns None when the database file is absent, the table has not
        been created, or the key is not found.
        """
        import sqlite3

        from backend.src.common.paths import kb_dir

        kb_path = os.fspath(kb_dir() / "kb.sqlite")
        if not os.path.exists(kb_path):
            return None
        try:
            conn = sqlite3.connect(kb_path, timeout=2)
            row = conn.execute("select value from config where key = ?", (key,)).fetchone()
            conn.close()
            return row[0] if row else None
        except Exception:
            return None

    def _get_sparse_embeddings(self):
        """Lazy-init FastEmbed's Qdrant/bm25 sparse encoder on first use.

        Returns None when the BM25 runtime is broken, signaling callers
        to fall back to dense-only retrieval mode.
        """
        if not _check_bm25():
            return None
        if not hasattr(self, "_sparse_embeddings_cache") or self._sparse_embeddings_cache is None:
            from langchain_qdrant import FastEmbedSparse

            logger.info("Loading FastEmbed Qdrant/bm25 sparse encoder…")
            self._sparse_embeddings_cache = FastEmbedSparse(model_name="Qdrant/bm25")
            logger.info("FastEmbed Qdrant/bm25 loaded.")
        return self._sparse_embeddings_cache

    def _get_user_collection_name(self, user_id: str) -> str:
        """Return the Qdrant collection name for a user.

        Computes the name without instantiating the vectorstore.
        """
        safe_user_id = re.sub(r"[^a-zA-Z0-9_]", "_", user_id)
        return f"user_{safe_user_id}_{self._get_collection_suffix()}"

    def _invalidate_user_collection(self, user_id: str) -> None:
        """Drop the cached QdrantVectorStore wrapper for a user.

        Evicts the cached vector store instance from memory.
        """
        self._vectorstore_cache.pop(user_id, None)

    def _reset_user_collection_in_place(self, user_id: str) -> None:
        """Drop a user's Qdrant collection and evict cached state.

        Used when indexing encounters corruption and requires clean
        recreation of the user's vector store.
        """
        try:
            client = self._get_qdrant_client()
            collection_name = self._get_user_collection_name(user_id)
            try:
                client.delete_collection(collection_name=collection_name)
                logger.info(f"Deleted Qdrant collection {collection_name} for user {user_id}")
            except Exception as exc:
                # Likely "collection not found" — benign.
                logger.info(f"Qdrant collection delete for {user_id} skipped/failed: {exc!r}")
        finally:
            self._invalidate_user_collection(user_id)

    def _get_parent_store_path(self, user_id: str) -> str:
        from backend.src.common.uploads import get_parent_store_path

        return os.fspath(get_parent_store_path(user_id))

    def _load_parent_store(self, user_id: str) -> dict[str, Any]:
        """Load the per-user parent store from disk.

        Values can be either a bare ``str`` (legacy entries from
        before offset-tracking shipped) or a ``dict`` carrying
        ``{text, body_start, body_end, page, section_title}`` (current
        format). Callers that need normalization should go through
        ``_get_parent_data``.
        """
        path = self._get_parent_store_path(user_id)
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_parent_store(self, user_id: str, store: dict[str, Any]) -> None:
        path = self._get_parent_store_path(user_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, temp_path = tempfile.mkstemp(
            dir=os.path.dirname(path),
            prefix=os.path.basename(path),
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(store, f, ensure_ascii=False)
            os.replace(temp_path, path)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def _delete_existing_sources(self, user_id: str, source_names: list[str]) -> None:
        """Delete existing chunks for sources that are being re-uploaded."""
        if not source_names:
            return

        from qdrant_client.http import models as qmodels

        # Force collection creation if first upload — saves an extra
        # roundtrip vs catching the "collection not found" exception.
        self._get_user_collection(user_id)

        client = self._get_qdrant_client()
        collection_name = self._get_user_collection_name(user_id)
        unique_sources = sorted(set(source_names))

        try:
            client.delete(
                collection_name=collection_name,
                points_selector=qmodels.FilterSelector(
                    filter=qmodels.Filter(
                        should=[
                            qmodels.FieldCondition(
                                key="metadata.source",
                                match=qmodels.MatchValue(value=name),
                            )
                            for name in unique_sources
                        ]
                    )
                ),
            )
            logger.info(
                f"Replaced existing indexed chunks for {unique_sources} from user {user_id}'s Qdrant collection"
            )
            self._cleanup_parent_store(user_id)
        except Exception as exc:
            # Common when the collection has zero matching points; treat
            # as a no-op rather than failing the whole upload.
            logger.info(
                f"Qdrant filter-delete for {user_id} sources={unique_sources} returned: {exc!r}"
            )

    def process_and_index_pdfs_with_texts(
        self,
        pdf_paths: list[str],
        parser_type: str = "pymupdf",
        user_id: str = "default",
    ):
        """Extract, chunk, and index a batch of PDFs into Qdrant.

        Parses documents in parallel across a thread pool, isolating
        individual file parse failures. Batches embedding and insertion
        every index_flush_size files to bound memory consumption. Writes
        dense and sparse BM25 vectors simultaneously.

        Returns a tuple of (source_names, extracted_texts).
        """
        total_started = time.perf_counter()
        extracted_texts: dict[str, str] = {}
        source_names = [os.path.basename(path) for path in pdf_paths]
        failed_files: list[dict[str, str]] = []

        # Remove prior versions of sources to ensure clean insertion.
        self._delete_existing_sources(user_id, source_names)

        # Pre-initialize shared converter and splitter instances on the
        # main thread before worker threads spawn to avoid races.
        if parser_type == "docling":
            try:
                _ = self._docling_converter
            except Exception:
                pass
        try:
            self._get_semantic_splitter()
        except Exception:
            pass

        # Parallelism bounded by upload_workers configuration.
        from concurrent.futures import ThreadPoolExecutor, as_completed

        workers = max(1, int(getattr(config, "upload_workers", 4)))
        flush_size = max(1, int(getattr(config, "index_flush_size", 50)))

        def _parse_one(path: str) -> dict[str, Any]:
            file_started = time.perf_counter()
            source = os.path.basename(path)
            try:
                docs, extracted_text, parent_chunks = self._process_pdf(
                    path,
                    user_id=user_id,
                    parser_type=parser_type,
                )
                return {
                    "ok": True,
                    "path": path,
                    "source": source,
                    "docs": docs,
                    "parents": parent_chunks,
                    "text": extracted_text or "",
                    "ms": (time.perf_counter() - file_started) * 1000,
                }
            except Exception as e:
                logger.exception(f"Parse failed for {source}: {e}")
                return {
                    "ok": False,
                    "path": path,
                    "source": source,
                    "error": str(e)[:300],
                    "ms": (time.perf_counter() - file_started) * 1000,
                }

        parse_and_chunk_ms = 0.0
        embed_ms = 0.0
        embed_calls = 0
        embed_texts = 0
        index_total_ms = 0.0
        total_chunk_count = 0

        # Accumulation buffers flushed every flush_size documents.
        buf_docs: list[Any] = []
        buf_parents: list[dict[str, Any]] = []
        buf_files = 0

        def _flush() -> None:
            nonlocal buf_docs, buf_parents, buf_files
            nonlocal embed_ms, embed_calls, embed_texts, index_total_ms
            nonlocal total_chunk_count
            if not buf_docs and not buf_parents:
                buf_docs, buf_parents, buf_files = [], [], 0
                return
            # 1. Persist parent chunks for the current batch.
            if buf_parents:
                self._add_parents(user_id, buf_parents)
            # 2. Embed and insert child chunks for the current batch.
            if buf_docs:
                sanitized = _sanitize_documents_for_qdrant(buf_docs)
                self.embeddings.begin_timing_session()
                index_started = time.perf_counter()
                try:
                    vectorstore = self._get_user_collection(user_id)
                    vectorstore.add_documents(sanitized)
                except Exception as e:
                    # Same recovery path as before — Qdrant
                    # dimension/collection errors recoverable by
                    # invalidating cache + (if corrupt) wiping the
                    # collection.
                    msg = str(e).lower()
                    is_corrupt = (
                        "wrong vector size" in msg
                        or "wrong vector dimension" in msg
                        or ("collection" in msg and "not found" in msg)
                        or "404" in msg
                    )
                    logger.warning(
                        f"Indexing failed for user {user_id} ({e!r}); "
                        f"{'resetting Qdrant collection and ' if is_corrupt else ''}"
                        f"invalidating cached client and retrying once."
                    )
                    self._invalidate_user_collection(user_id)
                    if is_corrupt:
                        self._reset_user_collection_in_place(user_id)
                    vectorstore = self._get_user_collection(user_id)
                    vectorstore.add_documents(sanitized)
                finally:
                    embed_stats = self.embeddings.consume_timing_session()
                index_total_ms += (time.perf_counter() - index_started) * 1000
                embed_ms += float(embed_stats.get("total_ms", 0.0))
                embed_calls += int(embed_stats.get("calls", 0))
                embed_texts += int(embed_stats.get("texts", 0))
                total_chunk_count += len(buf_docs)
            buf_docs, buf_parents, buf_files = [], [], 0

        # Drive parse in parallel; consume results in COMPLETION
        # order (not submission order) so straggling Docling files
        # don't stall the flush pipeline.
        if workers > 1 and len(pdf_paths) > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                future_to_path = {pool.submit(_parse_one, p): p for p in pdf_paths}
                for fut in as_completed(future_to_path):
                    res = fut.result()
                    parse_and_chunk_ms += float(res.get("ms", 0.0))
                    if not res["ok"]:
                        failed_files.append({"source": res["source"], "error": res["error"]})
                        continue
                    docs = res["docs"]
                    parents = res["parents"]
                    if res.get("text"):
                        extracted_texts[res["source"]] = res["text"]
                    logger.info(
                        "RAG upload phase: parser=%s file=%s parse_and_chunk=%.1fms chunks=%s extracted_chars=%s",
                        parser_type,
                        res["source"],
                        res["ms"],
                        len(docs),
                        len(res.get("text") or ""),
                    )
                    buf_docs.extend(docs)
                    buf_parents.extend(parents)
                    buf_files += 1
                    if buf_files >= flush_size:
                        _flush()
        else:
            # Sequential path — single thread, single file, or
            # explicit ``upload_workers=1``.
            for path in pdf_paths:
                res = _parse_one(path)
                parse_and_chunk_ms += float(res.get("ms", 0.0))
                if not res["ok"]:
                    failed_files.append({"source": res["source"], "error": res["error"]})
                    continue
                docs = res["docs"]
                parents = res["parents"]
                if res.get("text"):
                    extracted_texts[res["source"]] = res["text"]
                logger.info(
                    "RAG upload phase: parser=%s file=%s parse_and_chunk=%.1fms chunks=%s extracted_chars=%s",
                    parser_type,
                    res["source"],
                    res["ms"],
                    len(docs),
                    len(res.get("text") or ""),
                )
                buf_docs.extend(docs)
                buf_parents.extend(parents)
                buf_files += 1
                if buf_files >= flush_size:
                    _flush()

        # Final flush — anything still in buffers.
        _flush()

        store_overhead_ms = max(index_total_ms - embed_ms, 0.0)
        total_elapsed_ms = (time.perf_counter() - total_started) * 1000
        if total_chunk_count:
            logger.info(
                "RAG upload timings: parser=%s user=%s files=%s ok=%s failed=%s chunks=%s %s embed_calls=%s embed_texts=%s total=%.1fms",
                parser_type,
                user_id,
                len(pdf_paths),
                len(pdf_paths) - len(failed_files),
                len(failed_files),
                total_chunk_count,
                _format_phase_timings(
                    {
                        "parse_and_chunk": parse_and_chunk_ms,
                        "embed": embed_ms,
                        "store_overhead": store_overhead_ms,
                        "index_total": index_total_ms,
                    }
                ),
                embed_calls,
                embed_texts,
                total_elapsed_ms,
            )
        else:
            logger.info(
                "RAG upload timings: parser=%s user=%s files=%s ok=%s failed=%s chunks=0 parse_and_chunk=%.1fms total=%.1fms",
                parser_type,
                user_id,
                len(pdf_paths),
                len(pdf_paths) - len(failed_files),
                len(failed_files),
                parse_and_chunk_ms,
                total_elapsed_ms,
            )
        if failed_files:
            logger.warning(
                "RAG upload completed with %d failures: %s",
                len(failed_files),
                [f["source"] for f in failed_files],
            )

        return source_names, extracted_texts

    def _add_parents(self, user_id: str, parent_chunks: list[dict[str, Any]]) -> None:
        """Persist parents to disk.

        Each parent is stored as a dict carrying the chunk text plus
        optional offset metadata. The offsets pin the parent's body
        text to a precise ``[body_start, body_end)`` char range in
        the saved paper markdown — used at render time to anchor
        citation highlights without fuzzy matching.

        Backwards compatible: writes the new dict shape, but readers
        in this module also accept the legacy bare-string shape from
        parent stores written by older builds.
        """
        store = self._load_parent_store(user_id)
        for p in parent_chunks:
            entry: dict[str, Any] = {"text": p["text"]}
            for key in ("body_start", "body_end", "page", "section_title"):
                if p.get(key) is not None:
                    entry[key] = p[key]
            store[p["parent_id"]] = entry
        self._save_parent_store(user_id, store)

    def _get_parent_data(self, parent_id: str, user_id: str) -> dict[str, Any]:
        """Return the parent entry as a dict regardless of which
        on-disk shape produced it. Legacy bare-string entries are
        normalized to ``{"text": <str>}`` so callers don't branch."""
        store = self._load_parent_store(user_id)
        raw = store.get(parent_id)
        if raw is None:
            return {}
        if isinstance(raw, str):
            return {"text": raw}
        if isinstance(raw, dict):
            return raw
        # Defensive: unknown shape — coerce to text only
        return {"text": str(raw)}

    def _get_kb_parent_data(self, parent_id: str) -> dict[str, Any]:
        """Read parent data from the KB's ``kb.sqlite`` parents table.

        Returns the same dict shape as ``_get_parent_data`` so the
        citation pipeline works unchanged.
        """
        import sqlite3

        kb_path = os.fspath(kb_dir() / "kb.sqlite")
        if not os.path.exists(kb_path):
            return {}
        try:
            conn = sqlite3.connect(kb_path)
            row = conn.execute(
                "SELECT text, section_title, body_start, body_end, page "
                "FROM parents WHERE parent_id = ?",
                (parent_id,),
            ).fetchone()
            conn.close()
            if row is None:
                return {}
            return {
                "text": row[0] or "",
                "section_title": row[1] or "",
                "body_start": row[2],
                "body_end": row[3],
                "page": row[4],
            }
        except Exception:
            return {}

    @staticmethod
    def _find_page_for_offset(full_text: str, offset: int) -> int | None:
        """Recover 1-based page number for char offset inside full_text.

        Scans page comment boundaries emitted by PyMuPDF and returns
        the page number of the preceding marker.
        """
        if offset is None or offset < 0 or not full_text:
            return None
        page_pattern = re.compile(r"<!--\s*Page\s+(\d+)\s*-->")
        last_page: int | None = None
        scan_to = min(max(offset, 1), len(full_text))
        for match in page_pattern.finditer(full_text, 0, scan_to):
            try:
                last_page = int(match.group(1))
            except (TypeError, ValueError):
                continue
        return last_page

    def _cleanup_parent_store(self, user_id: str) -> None:
        """Prune parent chunks no longer referenced by any child in Qdrant."""
        # Force collection creation if needed (idempotent on existing).
        self._get_user_collection(user_id)
        client = self._get_qdrant_client()
        collection_name = self._get_user_collection_name(user_id)

        try:
            active_parent_ids: set = set()
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
                    metadata = payload.get("metadata", {}) or {}
                    pid = metadata.get("parent_id")
                    if pid:
                        active_parent_ids.add(pid)
                if offset is None:
                    break

            store = self._load_parent_store(user_id)
            new_store = {k: v for k, v in store.items() if k in active_parent_ids}
            self._save_parent_store(user_id, new_store)
        except Exception:
            # Collection might not exist yet or scan can fail mid-way;
            # parent store pruning is best-effort cleanup, not critical
            # for correctness.
            pass

    def _extract_pdf_metadata(self, pdf_path: str) -> dict[str, str]:
        """Extract DOI, authors, and journal from PDF metadata."""
        metadata = {"authors": "", "doi": "", "journal": "", "title": ""}
        try:
            import pymupdf

            doc = pymupdf.open(pdf_path)

            # Try PDF document info first
            pdf_info = doc.metadata or {}
            if pdf_info.get("author"):
                metadata["authors"] = pdf_info["author"]
            if pdf_info.get("title"):
                metadata["title"] = pdf_info["title"].strip()

            # Extract text from first 2 pages for regex-based extraction
            first_pages_text = ""
            for i in range(min(2, len(doc))):
                first_pages_text += doc[i].get_text("text") + "\n"
            doc.close()

            # DOI pattern
            doi_match = re.search(r'(10\.\d{4,}/[^\s,;"\'>]+)', first_pages_text)
            if doi_match:
                metadata["doi"] = doi_match.group(1).rstrip(".")

            # Journal detection: look for common patterns
            journal_patterns = [
                r"(?:Published in|Journal of|Proceedings of)[:\s]+([^\n]+)",
                r"(?:^|\n)([A-Z][a-z]+(?: [A-Z][a-z]+)* (?:Journal|Review|Letters|Research|Science|Chemistry|Pharmacology|Phytochemistry|Biochemistry|Biology)[^\n]*)",
            ]
            for pattern in journal_patterns:
                match = re.search(pattern, first_pages_text, re.IGNORECASE)
                if match:
                    metadata["journal"] = match.group(1).strip()[:100]
                    break

        except Exception as e:
            logger.warning(f"Metadata extraction failed for {pdf_path}: {e}")
        return metadata

    def _process_pdf(self, pdf_path: str, user_id: str = "default", parser_type: str = "pymupdf"):
        """PDF processing pipeline (Preserved from notebook)"""
        source = os.path.basename(pdf_path)

        # 0. Extract metadata (DOI, authors, journal)
        pdf_metadata = self._extract_pdf_metadata(pdf_path)

        # 1. Extract using selected parser
        if parser_type == "docling":
            try:
                return self._process_with_docling_skill(pdf_path, source, pdf_metadata, user_id)
            except Exception as e:
                logger.error(f"Docling skill processing failed, falling back: {e}")
                # Fall through to standard extraction if skill fails

        # Fallback/Standard Pipeline (PyMuPDF or Docling fallback)
        if parser_type == "pymupdf":
            full_text, tables = self._extract_with_pymupdf(pdf_path)
        else:
            full_text, tables = self._extract_with_docling(pdf_path)

        if not full_text:
            return [], "", []

        # Persist the extracted markdown so the citation preview
        # panel can serve it via /api/chat/files/{name}/markdown.
        # Best-effort: failures are logged inside the helper.
        self._save_paper_markdown(user_id, source, full_text)

        # 2. Section detection & Chunking (Regex-based fallback)
        sections = self._detect_sections(full_text)
        use_semantic_children = parser_type != "pymupdf"
        parent_chunks, chunks = self._chunk_by_sections(
            sections,
            tables,
            pdf_metadata,
            source,
            use_semantic_children=use_semantic_children,
            # Pass the full markdown so each parent can record an
            # exact char offset for the citation-highlight panel.
            full_text=full_text,
        )

        # Parents are returned to the caller (not written here) so
        # the upload orchestrator can batch them at flush time. This
        # is critical for thread-safe parallel parsing — each thread
        # produces its own parent_chunks; the orchestrator merges
        # them once per flush, avoiding the read-mutate-write race
        # over the per-user parent JSON.

        # 3. Deduplication
        unique_chunks = self._deduplicate_chunks(chunks)

        documents = []
        file_ext = os.path.splitext(source)[1].lower() or ".pdf"
        indexed_at = datetime.now(UTC).isoformat()
        for i, chunk in enumerate(unique_chunks):
            meta = chunk.get("metadata", {})
            meta["source"] = source
            meta["chunk_id"] = f"{source}_{i}"
            meta["parser_type"] = parser_type
            meta["file_type"] = file_ext
            meta["indexed_at"] = indexed_at
            meta["total_chunks"] = len(unique_chunks)
            # Add document-level metadata to every chunk
            meta["doc_title"] = pdf_metadata.get("title", "")
            meta["doc_authors"] = pdf_metadata.get("authors", "")
            meta["doc_doi"] = pdf_metadata.get("doi", "")
            meta["doc_journal"] = pdf_metadata.get("journal", "")
            from langchain_core.documents import Document

            documents.append(Document(page_content=chunk["text"], metadata=meta))

        return documents, full_text, parent_chunks

    def _save_paper_markdown(
        self,
        user_id: str,
        source: str,
        markdown: str,
    ) -> None:
        """Persist the extracted markdown view of a paper alongside
        its PDF so the citation preview panel can serve it later.
        Best-effort: a write failure is logged but never breaks
        ingest. Content is utf-8."""
        if not markdown or not source:
            return
        try:
            md_path = get_user_markdown_file_path(user_id, source)
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(markdown, encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to save extracted markdown for {source} (user {user_id}): {e}")

    def _process_with_docling_skill(
        self, pdf_path: str, source: str, pdf_metadata: dict, user_id: str = "default"
    ):
        """Process document using HybridChunker and parent-child chunking.

        Creates parent chunks (contextualized by HybridChunker) and
        child chunks (small chunks with contextual headers for
        embedding) consistent with the PyMuPDF processing pipeline.
        """
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
        from docling.document_converter import DocumentConverter, PdfFormatOption

        if self._docling_converter is None:
            logger.info("Initializing Docling DocumentConverter for Agent Skill...")
            pipeline_options = PdfPipelineOptions()
            pipeline_options.do_table_structure = True
            pipeline_options.table_structure_options.do_cell_matching = False
            pipeline_options.table_structure_options.mode = TableFormerMode.ACCURATE
            pipeline_options.do_ocr = False
            pipeline_options.do_code_enrichment = False
            pipeline_options.do_formula_enrichment = False

            self._docling_converter = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
            )

        abs_path = os.path.abspath(pdf_path)
        result = self._docling_converter.convert(
            abs_path,
            max_num_pages=config.max_num_pages,
            max_file_size=config.max_file_size,
        )

        if not result or not result.document:
            raise ValueError("Docling returned empty result")

        extracted_text = result.document.export_to_markdown()

        # Persist the extracted markdown so the citation preview
        # panel can serve it via /api/chat/files/{name}/markdown.
        self._save_paper_markdown(user_id, source, extracted_text)

        # Initialize HybridChunker (respects headers and structure)
        try:
            from docling.chunking import HybridChunker
            from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer

            _hybrid_available = True
        except ImportError:
            _hybrid_available = False
        if _hybrid_available:
            logger.info("Using Docling HybridChunker for semantic splitting...")
            from transformers import AutoTokenizer

            # Embedding models perform best capped at 512 tokens.
            tokenizer = HuggingFaceTokenizer(
                tokenizer=AutoTokenizer.from_pretrained(config.embedding_model),
                max_tokens=min(config.parent_chunk_size, 512),
            )
            chunker = HybridChunker(tokenizer=tokenizer, merge_peers=True)
            doc_chunks = list(chunker.chunk(result.document))

            file_ext = os.path.splitext(source)[1].lower() or ".pdf"
            indexed_at = datetime.now(UTC).isoformat()

            from langchain_text_splitters import RecursiveCharacterTextSplitter

            safety_splitter = RecursiveCharacterTextSplitter(
                chunk_size=config.parent_chunk_size,
                chunk_overlap=config.parent_chunk_overlap,
                separators=["\n\n", "\n", ". ", " ", ""],
            )

            doc_title = pdf_metadata.get("title", "")
            parent_chunks: list[dict[str, str]] = []
            all_child_chunks: list[dict[str, Any]] = []

            for i, chunk in enumerate(doc_chunks):
                # Contextualize prepends section breadcrumbs to text.
                chunk_text = chunker.contextualize(chunk)

                # Extract section title from headings
                headings = chunk.meta.headings or []
                section_title = headings[0] if headings else ""

                # Contextualize() already prepends section breadcrumbs;
                # prepend only doc_title to prevent duplicates.
                doc_header = f"{doc_title}\n\n" if doc_title else ""

                # Include source in parent ID to prevent collisions.
                parent_id = hashlib.md5(
                    f"docling::{source}::{section_title}::{chunk_text[:200]}".encode()
                ).hexdigest()

                # --- PARENT CHUNK ---
                # Split oversized chunks from HybridChunker as safety.
                if len(chunk_text) > config.parent_chunk_size * 2:
                    parent_texts = safety_splitter.split_text(chunk_text)
                else:
                    parent_texts = [chunk_text]

                for p_idx, p_text in enumerate(parent_texts):
                    pid = f"{parent_id}_p{p_idx}"
                    parent_with_header = doc_header + p_text
                    page_from_origin = getattr(chunk.meta.origin, "page_no", 0)

                    # Resolve body offset in the exported markdown.
                    # HybridChunker can reassemble across structural
                    # boundaries, so the contextualized chunk text
                    # may not be a contiguous substring — we fall back
                    # to searching for the chunk body without the
                    # contextualization breadcrumbs before giving up.
                    # ``matched_text`` tracks WHICH string was actually
                    # found so ``body_end`` reflects the real matched
                    # span (the previous version used ``len(p_text)``
                    # even when the raw_text fallback fired, which
                    # overshot the highlight when raw_text was
                    # shorter than the contextualized chunk).
                    body_start: int | None = None
                    body_end: int | None = None
                    if extracted_text and p_text.strip():
                        matched_text: str | None = None
                        pos = extracted_text.find(p_text)
                        if pos != -1:
                            matched_text = p_text
                        else:
                            raw_text = getattr(chunk, "text", "") or ""
                            if raw_text.strip():
                                raw_pos = extracted_text.find(raw_text)
                                if raw_pos != -1:
                                    pos = raw_pos
                                    matched_text = raw_text
                        if pos != -1 and matched_text:
                            # Same trimmed-body alignment as the
                            # PyMuPDF path. Without this, leading or
                            # trailing whitespace in ``matched_text``
                            # causes the recorded offset to disagree
                            # with the ``chunk_text`` field exposed
                            # to the frontend (which comes from
                            # ``_strip_to_body`` and is .strip()-ed).
                            leading_ws = len(matched_text) - len(matched_text.lstrip())
                            trailing_ws = len(matched_text) - len(matched_text.rstrip())
                            body_start = pos + leading_ws
                            body_end = pos + len(matched_text) - trailing_ws

                    parent_chunks.append(
                        {
                            "parent_id": pid,
                            "text": parent_with_header,
                            "section_title": section_title,
                            "body_start": body_start,
                            "body_end": body_end,
                            "page": page_from_origin or None,
                        }
                    )

                    # --- CHILD CHUNKS from this parent ---
                    child_texts = self._split_semantic_children(p_text)
                    for c_idx, c_text in enumerate(child_texts):
                        child_with_header = doc_header + c_text
                        all_child_chunks.append(
                            {
                                "text": child_with_header,
                                "metadata": {
                                    "source": source,
                                    "chunk_id": f"{source}_{i}_p{p_idx}_c{c_idx}",
                                    "parser_type": "docling_skill",
                                    "file_type": file_ext,
                                    "indexed_at": indexed_at,
                                    "content_type": "text",
                                    "doc_title": doc_title,
                                    "doc_authors": pdf_metadata.get("authors", ""),
                                    "doc_doi": pdf_metadata.get("doi", ""),
                                    "doc_journal": pdf_metadata.get("journal", ""),
                                    "page": page_from_origin,
                                    "section_title": section_title,
                                    "headings": headings,
                                    "parent_id": pid,
                                    "child_index": c_idx,
                                    "char_count": len(c_text),
                                    "word_count": len(c_text.split()),
                                },
                            }
                        )

            # Parents are returned (not persisted here) so the
            # upload orchestrator can batch-write them once per
            # flush — see ``_process_pdf`` for the full rationale.
            logger.info(
                f"Docling skill created {len(parent_chunks)} parents, {len(all_child_chunks)} children for {source}"
            )

            # Deduplicate children
            unique_children = self._deduplicate_chunks(all_child_chunks)
            total = len(unique_children)
            for c in unique_children:
                c["metadata"]["total_chunks"] = total
            from langchain_core.documents import Document

            return (
                [Document(page_content=c["text"], metadata=c["metadata"]) for c in unique_children],
                extracted_text,
                parent_chunks,
            )
        else:
            logger.warning("Docling chunking components missing, falling back to basic extraction")
            raise ImportError("Docling chunking components missing")

    def _extract_with_docling(self, pdf_path):
        logger.info(f"Starting detailed extraction with Docling for: {pdf_path}")
        try:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
            from docling.document_converter import DocumentConverter, PdfFormatOption

            if self._docling_converter is None:
                logger.info("Initializing Docling DocumentConverter (this may take a moment)...")
                pipeline_options = PdfPipelineOptions()
                pipeline_options.do_table_structure = True
                pipeline_options.table_structure_options.do_cell_matching = False
                pipeline_options.table_structure_options.mode = TableFormerMode.ACCURATE
                pipeline_options.do_ocr = False

                # Disable heavy enrichment to prevent background VLM
                # model downloads.
                pipeline_options.do_code_enrichment = False
                pipeline_options.do_formula_enrichment = False

                self._docling_converter = DocumentConverter(
                    format_options={
                        InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
                    }
                )

            # Use absolute path to avoid any confusion
            abs_path = os.path.abspath(pdf_path)
            result = self._docling_converter.convert(
                abs_path,
                max_num_pages=config.max_num_pages,
                max_file_size=config.max_file_size,
            )

            if not result or not result.document:
                logger.error(f"Docling returned empty result for {pdf_path}")
                return None, []

            md_text = result.document.export_to_markdown()

            docling_tables = []
            # Docling v2 exposes extracted tables via document.tables.
            if hasattr(result.document, "tables"):
                for table in result.document.tables:
                    try:
                        # Export table with document context.
                        table_md = table.export_to_markdown(doc=result.document)
                        pnum = 0
                        if hasattr(table, "prov") and table.prov and len(table.prov) > 0:
                            # Default to page 1 if found in provenance.
                            pnum = getattr(table.prov[0], "page_no", 1)
                        docling_tables.append({"content": table_md, "page": pnum})
                    except Exception as te:
                        logger.warning(f"Failed to export table in {pdf_path}: {te}")
                        continue

            logger.info(
                f"Docling extraction successful for {pdf_path} ({len(md_text)} chars, {len(docling_tables)} tables)"
            )
            return md_text, docling_tables
        except Exception as e:
            logger.error(f"Docling extraction failed for {pdf_path}: {str(e)}", exc_info=True)
            return None, []

    def _extract_with_pymupdf(self, pdf_path):
        """Extract markdown from PDF via PyMuPDF.

        Wraps backend.common.uploads.extract_paper_markdown so that
        ingestion and citation preview regeneration use identical text.
        Returns a tuple of (full_text, tables).
        """
        try:
            full_text = extract_paper_markdown(pdf_path)
            if not full_text.strip():
                logger.warning(f"pymupdf extracted empty text from {pdf_path}")
                return None, []
            return full_text, []
        except Exception as e:
            logger.warning(f"pymupdf extraction failed for {pdf_path}: {e}")
            return None, []

    def _detect_sections(self, text):
        """Detect paper sections from plain text or markdown.

        Recognizes standard scientific section titles across Markdown
        and PyMuPDF plaintext extractions.
        """
        SECTION_PATTERNS = [
            r"^(?:Abstract|Summary)\s*$",
            r"^(?:Introduction|Background|Literature Review|Related Work)\s*$",
            r"^(?:Methods|Methodology|Materials and Methods|Experimental(?: Setup)?|Procedure|Protocol)\s*$",
            r"^(?:Results|Findings)\s*$",
            r"^(?:Discussion)\s*$",
            r"^(?:Conclusion|Conclusions)\s*$",
            r"^(?:Acknowledgments?|Acknowledgements?)\s*$",
            r"^(?:References|Bibliography|Literature Cited)\s*$",
            r"^(?:Supplementary(?: Material| Information)?|Appendix(?:es)?)\s*$",
            r"^(?:Declarations?|Funding|Author Contributions|Ethics Statement|Data Availability|Conflicts? of Interest)\s*$",
        ]
        # Combine into one regex for efficiency
        section_regex = re.compile(
            "|".join(SECTION_PATTERNS),
            re.IGNORECASE | re.MULTILINE,
        )

        lines = text.split("\n")
        sections = []
        current = {"title": "Start", "level": 0, "content": [], "start": 0}

        for i, line in enumerate(lines):
            stripped = line.strip()
            is_header = False
            header_level = 0
            header_title = ""

            # 1. Markdown headers (from Docling).
            if stripped.startswith("#"):
                match = re.match(r"^(#{1,4})\s+(.+)$", stripped)
                if match:
                    is_header = True
                    header_level = len(match.group(1))
                    header_title = match.group(2).strip()

            # 2. Numbered sections (e.g., "1. Introduction").
            if not is_header and re.match(r"^\d+\.?\s+[A-Z]", stripped):
                is_header = True
                header_level = 1
                header_title = stripped

            # 3. Uppercase section headers.
            if (
                not is_header
                and re.match(r"^[A-Z][A-Z0-9&\s\-\.]{2,}$", stripped)
                and len(stripped) < 60
            ):
                # Verify it's a known section name
                if section_regex.search(stripped):
                    is_header = True
                    header_level = 1
                    header_title = stripped.title()

            # 4. Title-case section headers on isolated lines.
            if not is_header and len(stripped) < 50 and stripped:
                if section_regex.search(stripped):
                    is_header = True
                    header_level = 1
                    header_title = stripped
                # 5. Generic subsection detection for PyMuPDF plaintext.
                elif (
                    len(stripped) < 45
                    and 2 <= len(stripped.split()) <= 5
                    and not stripped.endswith(".")
                    and not stripped.endswith(":")
                    and stripped[0].isupper()
                    and not stripped.isupper()
                    and not stripped.isnumeric()
                    and i + 1 < len(lines)
                    and lines[i + 1].strip()
                    and not lines[i + 1].strip().startswith("#")
                ):
                    is_header = True
                    header_level = 2
                    header_title = stripped

            if is_header:
                # Save previous section
                if current["content"]:
                    current["text"] = "\n".join(current["content"])
                    sections.append(current)
                current = {
                    "title": header_title,
                    "level": header_level,
                    "content": [],
                    "start": i,
                }
                continue

            current["content"].append(line)

        if current["content"]:
            current["text"] = "\n".join(current["content"])
            sections.append(current)

        return sections

    def _chunk_by_sections(
        self,
        sections,
        tables,
        doc_metadata: dict[str, str] = None,
        source: str = "",
        use_semantic_children: bool = True,
        full_text: str = "",
    ):
        """Create hierarchical chunks with Markdown and contextual headers.

        Generates parent chunks (~2500 chars) for attribution and
        smaller child chunks for dense/sparse indexing. When full_text
        is supplied, assigns byte-exact character offsets to parents
        to support accurate citation highlighting in the frontend.

        Returns a tuple of (parent_chunks, all_chunks_for_indexing).
        """
        doc_metadata = doc_metadata or {}
        doc_title = doc_metadata.get("title", "")

        # Build contextual header prefix for this document
        def build_header(section_title: str) -> str:
            parts = []
            if doc_title:
                parts.append(doc_title)
            if section_title and section_title != "Start":
                parts.append(section_title)
            if parts:
                return " > ".join(parts) + "\n\n"
            return ""

        # Markdown splitters
        from langchain_text_splitters import MarkdownTextSplitter

        parent_splitter = MarkdownTextSplitter(
            chunk_size=config.parent_chunk_size,
            chunk_overlap=config.parent_chunk_overlap,
        )
        table_splitter = MarkdownTextSplitter(
            chunk_size=config.parent_chunk_size,
            chunk_overlap=config.parent_chunk_overlap,
        )

        parent_chunks: list[dict[str, str]] = []
        all_chunks: list[dict[str, Any]] = []

        # Tables: index directly, no parent-child
        for table in tables:
            content = table.get("content", "")
            if content.strip():
                table_md = f"## Table\n\n{content}"
                table_chunks = table_splitter.split_text(table_md)
                for i, tc in enumerate(table_chunks):
                    all_chunks.append(
                        {
                            "text": tc,
                            "metadata": {
                                "content_type": "table",
                                "page": table.get("page", 0),
                                "chunk_part": i + 1,
                            },
                        }
                    )

        # Sections: parent-child hierarchical chunking with Markdown
        for section in sections:
            text = section.get("text", "")
            if not text.strip():
                continue

            section_title = section.get("title", "")
            header = build_header(section_title)

            # Convert section to Markdown with header for the splitter
            # Skip "## Start" as it's not a real section
            if section_title and section_title != "Start":
                section_md = f"## {section_title}\n\n{text}"
                header_prefix = f"## {section_title}\n\n"
            else:
                section_md = text
                header_prefix = ""

            # Include source in parent_id to avoid cross-doc collisions.
            parent_id = hashlib.md5(f"{source}::{section_title}::{text[:200]}".encode()).hexdigest()

            # --- PARENT CHUNKS ---
            # Split section into parent-sized markdown chunks
            parent_texts = parent_splitter.split_text(section_md)
            for p_idx, p_text in enumerate(parent_texts):
                # Strip splitter-injected header to avoid duplicates.
                # The remaining ``p_text`` is the parent's body — the
                # same bytes we expect to find verbatim in saved paper
                # markdown (minus headers elided during detection).
                if header_prefix and p_text.startswith(header_prefix):
                    p_text = p_text[len(header_prefix) :]
                parent_with_header = header + p_text
                pid = f"{parent_id}_p{p_idx}"

                # Resolve the parent's body offset in the original
                # markdown. ``find()`` is byte-exact so this either
                # returns a precise [start, end) span or -1 (we
                # record nothing in that case and the frontend falls
                # back to fuzzy matching for this chunk only).
                #
                # We align the recorded span to the *trimmed* body —
                # advance past leading whitespace and pull back from
                # trailing whitespace. Without this, ``p_text`` from
                # the splitter often carries a leading newline that
                # ``_strip_to_body`` (the function exposing
                # ``chunk_text`` to Pass 2 and the frontend) removes
                # via ``.strip()``. The recorded offsets would then
                # disagree with ``chunk_text`` by 1-2 whitespace
                # chars at the boundaries, breaking byte-exact
                # comparison and causing Pass 2 quote-verification
                # drift + frontend slice/chunk_text mismatch.
                body_start: int | None = None
                body_end: int | None = None
                page: int | None = None
                if full_text and p_text.strip():
                    pos = full_text.find(p_text)
                    if pos != -1:
                        leading_ws = len(p_text) - len(p_text.lstrip())
                        trailing_ws = len(p_text) - len(p_text.rstrip())
                        body_start = pos + leading_ws
                        body_end = pos + len(p_text) - trailing_ws
                        page = self._find_page_for_offset(full_text, body_start)

                parent_chunks.append(
                    {
                        "parent_id": pid,
                        "text": parent_with_header,
                        "section_title": section_title,
                        "body_start": body_start,
                        "body_end": body_end,
                        "page": page,
                    }
                )

                # --- CHILD CHUNKS (from this parent) ---
                # PyMuPDF uses faster child splitting; Docling keeps
                # semantic boundary splitting.
                if use_semantic_children:
                    child_texts = self._split_semantic_children(p_text)
                else:
                    from langchain_text_splitters import MarkdownTextSplitter

                    child_splitter = MarkdownTextSplitter(
                        chunk_size=config.child_chunk_size,
                        chunk_overlap=config.child_chunk_overlap,
                    )
                    child_texts = child_splitter.split_text(p_text)
                for c_idx, c_text in enumerate(child_texts):
                    # Prepend contextual header to child for embedding
                    child_with_header = header + c_text
                    child_meta: dict[str, Any] = {
                        "section_title": section_title,
                        "content_type": "text",
                        "parent_id": pid,
                        "child_index": c_idx,
                        # Cheap quantitative metadata (Qdrant payload).
                        # Useful for filtering and analytics; doesn't
                        # affect retrieval scoring.
                        "char_count": len(c_text),
                        "word_count": len(c_text.split()),
                    }
                    if page is not None:
                        child_meta["page"] = page
                    all_chunks.append(
                        {
                            "text": child_with_header,
                            "metadata": child_meta,
                        }
                    )

        return parent_chunks, all_chunks

    def _deduplicate_chunks(self, chunks):
        unique = []
        seen_hashes = set()
        for chunk in chunks:
            text = chunk.get("text", "")
            if not text.strip():
                continue
            norm = re.sub(r"\s+", " ", text.lower().strip())
            h = hashlib.md5(norm.encode()).hexdigest()
            if h not in seen_hashes:
                unique.append(chunk)
                seen_hashes.add(h)
        return unique


def _sanitize_metadata_value(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (str, int, float)):
        return value
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _sanitize_documents_for_qdrant(documents):
    from langchain_core.documents import Document

    sanitized = []
    for doc in documents:
        sanitized.append(
            Document(
                page_content=doc.page_content,
                metadata={
                    key: _sanitize_metadata_value(value) for key, value in doc.metadata.items()
                },
            )
        )
    return sanitized


def _format_phase_timings(timings_ms: dict[str, float]) -> str:
    ordered = []
    for key, value in timings_ms.items():
        ordered.append(f"{key}={value:.1f}ms")
    return ", ".join(ordered)
