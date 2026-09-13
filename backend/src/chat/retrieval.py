"""Retrieval and reranking pipeline for chat queries.

Provides hierarchical hybrid vector search, cross-encoder reranking,
MMR diversity filtering, and positional citation prompt assembly.
"""

import logging
import os
import re
from typing import Any

from backend.src.settings import (
    RAG_CONTEXT_RESERVE_TOKENS,
    RAG_FLASH_ATTENTION,
    RAG_MULTI_GPU,
)

logger = logging.getLogger(__name__)

from backend.src.chat.citations import _is_chrome_sentence
from backend.src.chat.embeddings import _build_cuda_model_kwargs, config
from backend.src.common.paths import kb_dir
from backend.src.settings import RAG_CONTEXT_WINDOW

# Re-exported for tests that reference these by module-attr.

try:
    from sentence_transformers import CrossEncoder
except Exception:  # allow tests / minimal envs to inject a stub later
    CrossEncoder = None  # type: ignore[assignment]


class _RetrievalMixin:
    """Mixin implementing retrieval, reranking, and context assembly."""

    @property
    def reranker(self):
        """Lazy-load the reranker on first access."""
        if self._reranker is not ...:
            return self._reranker
        with self._reranker_lock:
            if self._reranker is not ...:
                return self._reranker
            try:
                from sentence_transformers import CrossEncoder

                logger.info(f"Loading reranker: {config.reranker_model} on {self._device}...")
                kwargs: dict[str, Any] = {"max_length": config.reranker_max_length}

                if self._device.startswith("cuda"):
                    cuda_kwargs = _build_cuda_model_kwargs(
                        enable_flash_attn=RAG_FLASH_ATTENTION,
                        enable_multi_gpu=RAG_MULTI_GPU,
                    )
                    if "device_map" in cuda_kwargs:
                        kwargs["model_kwargs"] = cuda_kwargs
                    else:
                        kwargs["device"] = self._device
                        kwargs["model_kwargs"] = cuda_kwargs
                else:
                    kwargs["device"] = self._device

                self._reranker = CrossEncoder(config.reranker_model, **kwargs)
            except Exception as e:
                logger.warning(f"Failed to load reranker on {self._device}: {e}")
                if self._device == "mps":
                    try:
                        from sentence_transformers import CrossEncoder

                        logger.info("Retrying reranker load on CPU due to MPS failure...")
                        kwargs = {"max_length": config.reranker_max_length, "device": "cpu"}
                        self._reranker = CrossEncoder(config.reranker_model, **kwargs)
                        self._device = "cpu"
                        logger.info("Reranker loaded successfully on CPU fallback.")
                    except Exception as cpu_e:
                        logger.warning(f"Failed to load reranker on CPU fallback: {cpu_e}")
                        self._reranker = None
                else:
                    self._reranker = None
            return self._reranker

    @reranker.setter
    def reranker(self, value):
        """Allow tests and callers to inject a mock reranker directly."""
        self._reranker = value

    def _hybrid_search(
        self,
        question: str,
        vectorstore,  # langchain_qdrant.QdrantVectorStore (lazy import)
        filter_files: list[str] | None = None,
        k: int = None,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Hybrid search via Qdrant dense and BM25 sparse fusion.

        The vectorstore uses RetrievalMode.HYBRID, so similarity search
        delegates to Qdrant's server-side query_points with RRF fusion.
        No local BM25 cache or manual reciprocal rank fusion is needed.

        Returns a list of dictionaries with "doc", "rrf",
        "vector_score", and "normalized_score" keys for downstream
        ranking stages.
        """
        from qdrant_client.http import models as qmodels

        if k is None:
            k = config.top_k

        # Build optional source filter (any-of over selected files).
        # langchain-qdrant accepts a qdrant_client Filter directly.
        search_kwargs: dict[str, Any] = {"k": k}
        if filter_files:
            search_kwargs["filter"] = qmodels.Filter(
                should=[
                    qmodels.FieldCondition(
                        key="metadata.source",
                        match=qmodels.MatchValue(value=src),
                    )
                    for src in filter_files
                ]
            )

        try:
            fused = vectorstore.similarity_search_with_score(question, **search_kwargs)
        except Exception as e:
            logger.warning(f"Hybrid search failed ({e}); returning empty result list")
            fused = []

        # Wrap (doc, score) tuples in the dict shape downstream
        # callers read. The score IS the fused RRF score from Qdrant
        # — we keep both ``rrf`` and ``vector_score`` so existing
        # call sites that reference either name keep working.
        result_dicts: list[dict[str, Any]] = []
        for doc, score in fused:
            result_dicts.append(
                {
                    "doc": doc,
                    "rrf": float(score),
                    "vector_score": float(score),
                }
            )

        # Normalize scores to 0-100 range for display in the UI.
        if result_dicts:
            max_score = result_dicts[0]["rrf"]
            for r in result_dicts:
                r["normalized_score"] = round((r["rrf"] / max_score) * 100) if max_score > 0 else 0

        return result_dicts

    def _get_reranker_max_tokens(self) -> int:
        """Return loaded cross-encoder model's actual max token
        capacity.

        Introspects tokenizer and model configuration to prevent
        runtime tensor shape errors. Returns the minimum of:
          - Tokenizer model_max_length (ignoring unbound sentinels)
          - Model config max_position_embeddings (architectural ceiling)
          - Configured reranker_max_length (user limit)

        Defaults to 512 if introspection fails.
        """
        if self.reranker is None:
            return 512

        candidates = []
        try:
            tok_max = getattr(self.reranker.tokenizer, "model_max_length", None)
            # Tokenizers without an enforced limit set this to a
            # huge sentinel (~1e18). Filter values that are clearly
            # out of range for any real cross-encoder.
            if tok_max and 0 < tok_max < 8192:
                candidates.append(int(tok_max))
        except Exception:
            pass

        try:
            mdl = getattr(self.reranker, "model", None)
            mdl_cfg = getattr(mdl, "config", None) if mdl else None
            if mdl_cfg is not None:
                mpe = getattr(mdl_cfg, "max_position_embeddings", None)
                if mpe and 0 < mpe < 8192:
                    candidates.append(int(mpe))
        except Exception:
            pass

        try:
            cfg_max = int(config.reranker_max_length)
            if 0 < cfg_max < 8192:
                candidates.append(cfg_max)
        except Exception:
            pass

        if candidates:
            return min(candidates)
        return 512

    def _truncate_for_reranker(self, text: str, max_tokens: int) -> str:
        """Truncate text to at most max_tokens using reranker tokenizer.

        Cross-encoders evaluate pairs against a fixed sequence length.
        Token-exact truncation prevents tensor-shape mismatch errors
        when scoring long parent contexts, with a 4-character-per-token
        fallback if tokenization fails.
        """
        if not text or self.reranker is None or max_tokens <= 0:
            return text
        try:
            tokenizer = self.reranker.tokenizer
            ids = tokenizer.encode(text, add_special_tokens=False)
            if len(ids) <= max_tokens:
                return text
            return tokenizer.decode(ids[:max_tokens], skip_special_tokens=True)
        except Exception as e:
            logger.warning(
                f"Reranker tokenizer truncation failed ({e}); falling back to char estimate"
            )
            # Conservative char fallback (~4 chars per token for
            # English; we deliberately under-estimate to stay safe).
            return text[: max_tokens * 4]

    def _find_best_sentence(self, claim: str, chunk_text: str, exclude: tuple = ()) -> str:
        """Return sentence in chunk_text most relevant to claim via reranker.

        The exclude parameter holds already-used quote texts to assign
        distinct quotes when multiple claims cite one chunk. Falls back
        to the top-scoring sentence when all candidate sentences have
        been previously excluded.

        Falls back to the first non-trivial sentence if the reranker is
        unavailable, the chunk contains a single sentence, or scoring
        fails. LLM output identifies the chunk index, and deterministic
        cross-encoder scoring selects the specific supporting sentence.
        """
        if not chunk_text:
            return ""
        # Split paragraphs first (and strip markdown heading markers)
        # so a section heading cannot merge with the subsequent
        # paragraph into an artificially long sentence.
        paras = [p.strip() for p in re.split(r"\n\s*\n", chunk_text) if p.strip()]
        sentences = []
        for p in paras:
            p = re.sub(r"(?m)^#{1,6}\s*", "", p).strip()
            sentences.extend(s.strip() for s in re.split(r"(?<=[.!?])\s+", p) if s.strip())
        # Drop very short fragments (artifacts of bullet points,
        # abbreviations such as "et al.") to avoid ranking noise.
        sentences = [s for s in sentences if len(s) >= 20]
        # Drop publisher chrome (journal bars, DOI/URL lines, all-caps
        # headings) so quotes do not anchor on footers when body scores
        # are low. Falls back to unfiltered candidates if all sentences
        # matched chrome patterns.
        filtered = [s for s in sentences if not _is_chrome_sentence(s)]
        if filtered:
            sentences = filtered
        if not sentences:
            # Fall back to chunk prefix if sentence splitting yields
            # no usable candidates.
            return chunk_text[:300].strip()
        if len(sentences) == 1 or self.reranker is None:
            return sentences[0]
        try:
            # Budget tokens according to the model's actual maximum,
            # minus 8 tokens reserved for special tokens ([CLS], [SEP]).
            max_total = max(64, self._get_reranker_max_tokens() - 8)
            half = max_total // 2
            t_claim = self._truncate_for_reranker(claim, half)
            t_sentences = [self._truncate_for_reranker(s, half) for s in sentences]
            pairs = [[t_claim, s] for s in t_sentences]
            scores = self.reranker.predict(pairs)
            ranked = sorted(
                range(len(scores)),
                key=lambda i: float(scores[i]),
                reverse=True,
            )
            excluded = {self._normalize_for_match(q) for q in exclude}
            for i in ranked:
                if self._normalize_for_match(sentences[i]) not in excluded:
                    return sentences[i]
            return sentences[ranked[0]]
        except Exception as e:
            logger.warning(f"Sentence reranker scoring failed: {e}")
            return sentences[0]

    def _fallback_top_chunks_for_answer(
        self,
        answer: str,
        chunk_text_by_id: dict[str, str],
        top_n: int = 2,
    ) -> list[str]:
        """Used when the structured-output call returned nothing.
        Score every chunk against the full answer with the reranker
        and pick the top-N. Guarantees an answer is never uncited.

        Falls back further to "first N chunks in retrieval order"
        when the reranker is unavailable.
        """
        ids = list(chunk_text_by_id.keys())
        if not ids:
            return []
        if self.reranker is None:
            return ids[:top_n]
        try:
            import numpy as np
        except ImportError:
            return ids[:top_n]
        max_total = max(64, self._get_reranker_max_tokens() - 8)
        a_budget = min(160, max_total // 3)
        c_budget = max(64, max_total - a_budget)
        a_t = self._truncate_for_reranker(answer, a_budget)
        try:
            pairs = [
                [a_t, self._truncate_for_reranker(chunk_text_by_id[cid], c_budget)] for cid in ids
            ]
            scores = self.reranker.predict(pairs)
        except Exception as e:
            logger.warning(f"Fallback chunk-rerank failed: {e}")
            return ids[:top_n]
        order = np.argsort(scores)[::-1]
        return [ids[int(i)] for i in order[:top_n]]

    @staticmethod
    def _diversify_chunks(
        chunks: list[dict[str, Any]],
        max_keep: int,
        similarity_threshold: float = 0.60,
    ) -> list[dict[str, Any]]:
        """Greedy diversity filter using bigram overlap coefficient.

        Iterates through rerank-ordered candidates and retains a chunk
        only if its lexical bigram overlap coefficient
        (|A ∩ B| / min(|A|, |B|)) with every selected chunk is below
        similarity_threshold. The overlap coefficient is chosen over
        Jaccard because it flags subset containment when chunks differ
        in length (e.g. short summaries contained in full sections).

        similarity_threshold=0.60 is empirically calibrated for
        scientific text: same-section chunks score 0.70+, while
        distinct-section chunks score <0.40.
        """
        if not chunks:
            return []

        def bigrams(text: str) -> set:
            words = re.findall(r"\w+", text.lower())
            if len(words) < 2:
                return set(words)
            return set(zip(words, words[1:]))

        selected: list[dict[str, Any]] = []
        selected_grams: list[set] = []

        for cand in chunks:
            if len(selected) >= max_keep:
                break
            cand_text = (cand.get("doc").page_content if cand.get("doc") else "") or ""
            cand_grams = bigrams(cand_text)
            if not cand_grams:
                # Keep tiny or empty chunks (such as tables or headings)
                # because they cannot dominate the LLM context.
                selected.append(cand)
                selected_grams.append(cand_grams)
                continue

            too_similar = False
            for sel_grams in selected_grams:
                if not sel_grams:
                    continue
                inter = len(cand_grams & sel_grams)
                denom = min(len(cand_grams), len(sel_grams))
                if denom and inter / denom >= similarity_threshold:
                    too_similar = True
                    break
            if not too_similar:
                selected.append(cand)
                selected_grams.append(cand_grams)

        return selected

    @staticmethod
    def _apply_context_budget(
        items: list[dict[str, Any]],
        budget_chars: int,
    ) -> tuple:
        """Partition retrieved items into in-context versus over-budget.

        Expects items containing chunk_id, block, and record. Sets
        record["context_status"] to "full" or "omitted_budget" and
        returns (context_parts, sources, citable_sources).
        The top item is always retained regardless of budget to ensure
        the downstream LLM receives minimal grounding context.
        """
        context_parts: list[str] = []
        sources: list[dict[str, Any]] = []
        citable_sources: list[dict[str, Any]] = []
        used = 0
        for i, item in enumerate(items):
            record = item["record"]
            block = item["block"]
            if i == 0 or used + len(block) <= budget_chars:
                record["context_status"] = "full"
                context_parts.append(block)
                citable_sources.append(record)
                used += len(block)
            else:
                record["context_status"] = "omitted_budget"
            sources.append(record)
        return context_parts, sources, citable_sources

    async def _prepare_query(
        self,
        question: str,
        filter_files: list[str] | None = None,
        user_id: str = "default",
        chat_history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Execute retrieval, reranking, and assembly of the LLM prompt.

        Returns either:
          - {"answer": str, "sources": []} when context is insufficient
            to short-circuit invocation.
          - {"messages": list, "sources": list, "citable_sources": list,
            "is_kb_mode": bool} ready for downstream invocation.
        """
        user_files = self.list_indexed_files(user_id)
        # KB mode is active when the user has no uploaded files or
        # explicitly unchecked all files in the filter.
        is_kb_mode = (not user_files) or (filter_files is not None and len(filter_files) == 0)
        if not is_kb_mode:
            vectorstore = self._get_user_collection(user_id)
            effective_filter = filter_files
            _resolve_parent = lambda pid: self._get_parent_data(pid, user_id)
        else:
            kb_store = self._get_kb_collection()
            if kb_store is None:
                return {
                    "answer": "I couldn't find enough relevant context in the knowledge base to answer that question.",
                    "sources": [],
                    "is_kb_mode": True,
                }
            vectorstore = kb_store
            effective_filter = None
            _resolve_parent = self._get_kb_parent_data

        # 1. Retrieve initial child chunks from store (up to 200).
        search_results = self._hybrid_search(
            question,
            vectorstore,
            effective_filter,
            k=config.retrieve_k,
            user_id=user_id,
        )

        # 2. Cross-encoder reranking across all retrieved children.
        reranked_children = []
        if self.reranker and search_results:
            candidate_results = []
            filtered_candidates = []
            try:
                import numpy as np

                candidate_limit = int(config.rerank_candidate_k)
                if candidate_limit <= 0:
                    logger.info(
                        "RAG rerank skipped: non-positive candidate limit=%s",
                        candidate_limit,
                    )
                    reranked_children = search_results
                    candidate_results = []
                else:
                    candidate_results = search_results[:candidate_limit]
                blank_candidate_count = 0
                if candidate_results:
                    for result in candidate_results:
                        page_content = getattr(result.get("doc"), "page_content", "") or ""
                        if not page_content.strip():
                            blank_candidate_count += 1
                            continue
                        filtered_candidates.append(result)

                    candidate_lengths = [
                        len(res["doc"].page_content.strip()) for res in filtered_candidates
                    ]
                    logger.info(
                        "RAG rerank boundary: total_candidates=%s filtered_candidates=%s blank_candidates=%s min_chars=%s max_chars=%s",
                        len(candidate_results),
                        len(filtered_candidates),
                        blank_candidate_count,
                        min(candidate_lengths) if candidate_lengths else 0,
                        max(candidate_lengths) if candidate_lengths else 0,
                    )

                    if not filtered_candidates:
                        reranked_children = search_results
                        raise ValueError("No non-empty rerank candidates available")

                    query_text = question.strip()
                    # Guard against empty queries — cross-encoder
                    # tokenizers can produce zero-length sequences,
                    # causing attention reshape to fail on 0 elements
                    # in [batch, 0, -1, head_dim].
                    if not query_text:
                        reranked_children = filtered_candidates
                        raise ValueError("Empty query — skipping rerank")

                    # Instruction-aware reranking: prepend the domain
                    # instruction to each query in the pair (honored
                    # by instruction-tuned cross-encoders; inert
                    # otherwise).
                    if config.reranker_instruction:
                        instructed_query = f"{config.reranker_instruction}\n{query_text}"
                    else:
                        instructed_query = query_text

                    # Drop pairs where either side is whitespace-only —
                    # cross-encoder tokenizers can yield zero-length
                    # tensors for these.
                    pairs = []
                    valid_candidates = []
                    reranker_budget = self._get_reranker_max_tokens() // 2
                    for res in filtered_candidates:
                        passage = (res["doc"].page_content or "").strip()
                        if not passage:
                            continue
                        pairs.append(
                            [
                                self._truncate_for_reranker(instructed_query, reranker_budget),
                                self._truncate_for_reranker(passage, reranker_budget),
                            ]
                        )
                        valid_candidates.append(res)
                    if not pairs:
                        reranked_children = filtered_candidates
                        raise ValueError("No tokenizable rerank pairs after filtering")
                    filtered_candidates = valid_candidates

                    rerank_batch_size = max(1, int(config.rerank_batch_size))
                    rerank_scores = []
                    for batch_start in range(0, len(pairs), rerank_batch_size):
                        batch_pairs = pairs[batch_start : batch_start + rerank_batch_size]
                        batch_scores = self.reranker.predict(batch_pairs)
                        rerank_scores.extend(list(batch_scores))

                    if len(rerank_scores) != len(filtered_candidates):
                        raise ValueError(
                            f"Reranker score count mismatch: expected {len(filtered_candidates)} got {len(rerank_scores)}"
                        )

                    scores = np.array(rerank_scores, dtype=float)
                    if not np.isfinite(scores).all():
                        raise ValueError("Reranker returned non-finite scores")

                    for i, score in enumerate(scores):
                        filtered_candidates[i]["rerank_score"] = float(score)

                    # Normalize scores to [0, 1] via min-max scaling.
                    min_s, max_s = scores.min(), scores.max()
                    if max_s > min_s:
                        normalized = (scores - min_s) / (max_s - min_s)
                    else:
                        normalized = np.ones_like(scores) * 0.5

                    for i, r in enumerate(filtered_candidates):
                        r["normalized_score"] = round(float(normalized[i]) * 100)

                    # Filter by configured relevance threshold.
                    reranked_children = [
                        r
                        for i, r in enumerate(filtered_candidates)
                        if normalized[i] >= config.rerank_threshold
                    ]
                    if not reranked_children:
                        logger.warning(
                            "RAG rerank produced zero survivors after threshold=%s; falling back to retrieval order.",
                            config.rerank_threshold,
                        )
                        reranked_children = filtered_candidates or search_results
                    else:
                        # Sort candidates by rerank score descending.
                        reranked_children.sort(key=lambda x: x["rerank_score"], reverse=True)
            except Exception as e:
                logger.error(f"Reranking failed: {e}")
                reranked_children = filtered_candidates or search_results
        else:
            reranked_children = search_results

        # 3. Parent-Child Resolution: resolve filtered children to
        # unique parents. Candidates are oversampled up to
        # 3x max_parents to ensure the diversity filter operates over
        # a sufficiently broad set of distinct paper sections.
        candidate_pool_size = max(config.max_parents * 3, 6)
        parent_ids_seen: set[str] = set()
        candidate_parents: list[dict[str, Any]] = []

        # Batch-fetch parent contexts for KB mode in one query.
        kb_parent_cache: dict[str, dict[str, Any]] = {}
        if is_kb_mode:
            import sqlite3 as _sqlite3

            from scripts.ingest_kb import get_parent_contexts

            all_parent_ids = list(
                {
                    r["doc"].metadata.get("parent_id")
                    for r in reranked_children
                    if r["doc"].metadata.get("content_type", "text") == "text"
                    and r["doc"].metadata.get("parent_id")
                }
            )
            kb_path = os.fspath(kb_dir() / "kb.sqlite")
            if os.path.exists(kb_path) and all_parent_ids:
                _conn = _sqlite3.connect(kb_path)
                kb_parent_cache = get_parent_contexts(all_parent_ids, _conn)
                _conn.close()

        for result in reranked_children:
            d = result["doc"]
            ctype = d.metadata.get("content_type", "text")
            if ctype != "text":
                # Tables pass through directly (no parent resolution).
                candidate_parents.append(result)
                continue

            parent_id = d.metadata.get("parent_id")
            if not parent_id or parent_id in parent_ids_seen:
                continue

            parent_ids_seen.add(parent_id)
            if not is_kb_mode:
                parent_data = _resolve_parent(parent_id)
            else:
                parent_data = kb_parent_cache.get(parent_id, {})
            ptext = parent_data.get("text", "")
            if ptext:
                from langchain_core.documents import Document

                # Create a synthetic result with parent text. Carry
                # the parent's body_start/body_end/page offsets onto
                # the result dict so they survive into the source
                # output without us re-reading the parent store.
                candidate_parents.append(
                    {
                        "doc": Document(
                            page_content=ptext,
                            metadata=d.metadata,
                        ),
                        "rerank_score": result.get("rerank_score", 0),
                        "normalized_score": result.get("normalized_score", 0),
                        "body_start": parent_data.get("body_start"),
                        "body_end": parent_data.get("body_end"),
                        "page": parent_data.get("page"),
                    }
                )
            else:
                candidate_parents.append(result)

            if len(candidate_parents) >= candidate_pool_size:
                break

        # 3.5. Diversity filter: greedy selection dropping candidates
        # whose lexical bigram overlap with already-selected chunks
        # exceeds the threshold, preventing redundant citations.
        parent_results = self._diversify_chunks(candidate_parents, max_keep=config.max_parents)

        # 4. Build LLM context from resolved parents.
        #
        # Citation markers are assigned positionally per turn ("c1",
        # "c2", etc.) rather than globally across the conversation.
        # Scoping identifiers strictly to the current turn prevents
        # cross-turn citation leakage and hallucinated recall.
        # The "c" prefix disambiguates citations from literal numeric
        # bibliography references (e.g. "[1]") native to papers.
        items: list[dict[str, Any]] = []
        for chunk_index, result in enumerate(parent_results):
            d = result["doc"]
            score = result.get("normalized_score", 0)
            ctype = d.metadata.get("content_type", "text")
            src = d.metadata.get("source", "")
            sec = d.metadata.get("section_title", "")

            title = d.metadata.get("doc_title", "")
            authors = d.metadata.get("doc_authors", "")

            chunk_id = f"c{chunk_index + 1}"

            # Build a structured header for the context block.
            header_elements = [f"chunk_id={chunk_id}"]
            if title:
                header_elements.append(f"Title: {title}")
            if authors:
                header_elements.append(f"Authors: {authors}")
            header_elements.append(f"File: {src}")
            if sec:
                header_elements.append(f"Section: {sec}")

            header_str = " | ".join(header_elements)

            # Each chunk body is prefixed with [chunk_id] so the LLM can
            # reference it back using identical syntax.
            if ctype == "table":
                block = f"[{chunk_id}] [TABLE | {header_str}]:\n{d.page_content}"
            else:
                block = f"[{chunk_id}] [{header_str}]:\n{d.page_content}"

            parser_type = d.metadata.get("parser_type", "docling")
            # Strip section headings and titles so chunk_text matches
            # verbatim markdown body for exact frontend highlight.
            citable_text = self._strip_to_body(d.page_content, title=title)
            # Propagate character offsets (body_start/body_end)
            # to enable byte-exact highlighting on the frontend.
            source_record: dict[str, Any] = {
                "chunk_id": chunk_id,
                "source": src,
                "section": sec,
                "parser_type": parser_type,
                "score": score,
                "chunk_text": citable_text,
            }
            # For KB mode, carry paper-level metadata so the
            # reference builder can group chunks by paper and
            # emit Perplexity-style numbered references.
            if is_kb_mode:
                source_record["doc_title"] = d.metadata.get("doc_title", "")
                source_record["doc_doi"] = d.metadata.get("doc_doi", "")
            body_start = result.get("body_start")
            body_end = result.get("body_end")
            page = result.get("page") or d.metadata.get("page") or None
            if body_start is not None and body_end is not None:
                source_record["body_start"] = body_start
                source_record["body_end"] = body_end
            if page:
                source_record["page"] = page
            items.append({"chunk_id": chunk_id, "block": block, "record": source_record})

        # Context budget: ~4 chars per token, minus reserve for system
        # prompt, history, and answer. Over-budget sources are marked
        # and kept in the frame, but excluded from LLM context and
        # citations to avoid certifying text the model never saw.
        budget_chars = max(1000, (RAG_CONTEXT_WINDOW - RAG_CONTEXT_RESERVE_TOKENS) * 4)
        context_parts, sources, citable_sources = self._apply_context_budget(items, budget_chars)
        context = "\n\n".join(context_parts)

        if not context_parts:
            no_context_msg = (
                "I couldn't find enough relevant context in the knowledge base to answer that question."
                if is_kb_mode
                else "I couldn't find enough relevant context in the selected sources to answer that question."
            )
            return {
                "answer": no_context_msg,
                "sources": [],
                "is_kb_mode": is_kb_mode,
            }

        # Multi-turn messages: prompt injects context and instructs the
        # LLM to append [cN] markers. Provenance metadata in headers
        # (title, file, section, page) resolves references directly.
        system_msg = {
            "role": "system",
            "content": (
                "You are a scientific research assistant. Answer the "
                "question using ONLY the provided context from research "
                "papers above. Use clear markdown formatting (headings, "
                "lists, bold for key terms).\n\n"
                "Rules:\n"
                "1. Ground every factual claim in the supplied context. "
                "If the context does not contain enough information, say "
                "so explicitly rather than guessing.\n"
                "2. Be precise: prefer concrete numbers, dataset names, "
                "and quoted terminology from the context over vague "
                "summaries.\n"
                "3. Cite chunks you used: at the end of each sentence "
                "that draws on the context, append [cN] using the "
                "chunk_id from its header (e.g. [c1], [c2]). Use only "
                "IDs from this prompt, you may list several like "
                "[c1][c3]. Leave a sentence uncited only if it is not "
                "from the context."
            ),
        }

        messages = [system_msg]

        # Add conversation history (last 5 turns = 10 messages max).
        #
        # Rewrite citation markers in prior assistant turns ([cN],
        # bare [N], or whitespace-padded variants). Positional IDs
        # are turn-specific, so preserving literal [cN] markers in
        # history risks chunk misattribution on subsequent turns.
        #
        # Conversely, completely removing markers leads the LLM to
        # infer that citations are omitted and stop emitting them
        # (few-shot mimicry overriding system instructions).
        #
        # Replacing markers with "[†]" retains the citation cadence
        # for in-context imitation without leaking stale chunk IDs.
        marker_pattern = re.compile(r"\[\s*[Cc]?\s*\d+\s*\]")
        if chat_history:
            history_window = chat_history[-10:]
            for msg in history_window:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "assistant" and content:
                    content = marker_pattern.sub("[†]", content)
                messages.append({"role": role, "content": content})

        # Append current question with context.
        user_msg = f"""Context from research papers:
{context}

Question: {question}"""
        messages.append({"role": "user", "content": user_msg})

        return {
            "messages": messages,
            "sources": sources,
            "citable_sources": citable_sources,
            "is_kb_mode": is_kb_mode,
        }


_bm25_probe_done: bool = False


_bm25_probe_ok: bool = False


def _check_bm25() -> bool:
    """Return True if fastembed's BM25 stemmer works in this runtime."""
    global _bm25_probe_done, _bm25_probe_ok
    if _bm25_probe_done:
        return _bm25_probe_ok
    _bm25_probe_done = True
    try:
        import subprocess
        import sys

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import py_rust_stemmers;"
                "s = py_rust_stemmers.SnowballStemmer('english');"
                "s.stem_word('testing')",
            ],
            capture_output=True,
            timeout=30,
        )
        _bm25_probe_ok = result.returncode == 0
        if not _bm25_probe_ok:
            logger.warning(
                "BM25 sparse encoder disabled: py_rust_stemmers crashed "
                "(exit code %d). Falling back to dense-only retrieval. "
                "Upgrade py_rust_stemmers or Python to re-enable hybrid search.",
                result.returncode,
            )
        else:
            logger.info("BM25 sparse encoder probe passed — hybrid retrieval available.")
    except Exception as exc:
        logger.warning("BM25 probe could not run (%s), disabling sparse retrieval.", exc)
        _bm25_probe_ok = False
    return _bm25_probe_ok
