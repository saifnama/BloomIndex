"""RAG citation extraction, validation, and reference formatting mixin.

Provides post-generation citation resolution, grounding verification
against retrieved context chunks, and reference list generation for
chat responses.
"""

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

from backend.src.chat.llm import _quote_matches_chunk


class _CitationsMixin:
    async def _select_used_chunks(
        self,
        question: str,
        answer: str,
        sources: list[dict[str, Any]],
        timeout_seconds: float = 25.0,
        max_attempts: int = 2,
    ) -> list[str]:
        """Structured-output pass: identify chunk_ids drawn on by the LLM.

        Mirrors the validation-retry pattern popularized by
        ``instructor``: when the LLM's JSON response fails any of
        our shape/content checks (wrong key name, wrong type, all
        hallucinated ids, empty list), we re-prompt with the
        specific error so the model can correct itself instead of
        falling back to reranker-picked chunks. ``json_repair``
        still handles syntax-level malformations (trailing commas,
        code fences, single quotes, unclosed braces) on every
        attempt; ``max_attempts`` governs the *semantic* retry
        loop on top of that.

        Returns a list of validated chunk_ids in priority order
        (LLM's stated order). Empty list on terminal failure —
        caller should fall back to "use top-N reranked chunks for
        the whole answer."
        """
        if not answer.strip() or not sources:
            return []

        retrieved_ids = [
            s["chunk_id"] for s in sources if s.get("chunk_id") and s.get("chunk_text", "").strip()
        ]
        if not retrieved_ids:
            return []
        valid_set = set(retrieved_ids)

        # Compact chunk catalog — id + first ~400 chars per chunk so
        # the LLM can identify each one without re-streaming the full
        # body it already saw during the answering pass.
        chunks_block = "\n\n".join(
            f"[{s['chunk_id']}] {(s.get('chunk_text') or '')[:400]}"
            for s in sources
            if s.get("chunk_id")
        )

        base_prompt = (
            "You just wrote the ANSWER below using the source CHUNKS. "
            "List the chunk_ids you actually drew on to write that "
            "answer.\n\n"
            "Output ONLY a JSON object of the form:\n"
            '  {"chunk_ids": ["c1", "c3", ...]}\n\n'
            "Rules:\n"
            f"- Use ONLY ids from this list: {retrieved_ids}.\n"
            "- Order ids by how central they are to the answer (most "
            "central first).\n"
            "- Include every chunk that contributed a fact, number, or "
            "definition you used. Skip chunks you did not use.\n"
            "- The list MUST contain at least one chunk_id.\n"
            "- Do NOT include any prose, explanation, or extra fields.\n\n"
            f"QUESTION:\n{question}\n\n"
            f"ANSWER:\n{answer}\n\n"
            f"CHUNKS:\n{chunks_block}\n\n"
            "JSON:"
        )

        from json_repair import repair_json

        error_hint: str | None = None
        for attempt in range(max_attempts):
            # On retry, prepend an explicit correction block so the
            # model can see what went wrong with its previous output.
            # Hint is *prepended* (not appended) so it's the first
            # thing the model attends to in long prompts.
            if error_hint is not None:
                prompt = (
                    "Your previous response was rejected for the "
                    f"following reason:\n  {error_hint}\n\n"
                    "Retry with a corrected response that conforms "
                    "to the schema below.\n\n"
                    f"{base_prompt}"
                )
            else:
                prompt = base_prompt

            try:
                response = await self._invoke_llm(
                    prompt=prompt,
                    max_retries=1,
                    timeout_seconds=timeout_seconds,
                    response_format={"type": "json_object"},
                )
            except Exception as e:
                logger.warning(f"chunk-id selection LLM call failed: {e}")
                return []

            raw = (response.content or "").strip()
            if not raw:
                error_hint = (
                    "Your previous response was empty. Return a "
                    'JSON object: {"chunk_ids": ["c1", ...]}.'
                )
                continue

            try:
                parsed = repair_json(raw, return_objects=True)
            except Exception as e:
                logger.warning(f"chunk-id selection JSON parse failed (attempt {attempt + 1}): {e}")
                error_hint = (
                    "Your previous response could not be parsed as "
                    "JSON. Return a single object, no prose around it."
                )
                continue

            # Shape validation — every branch sets a precise
            # error_hint so the retry prompt can quote the specific
            # mistake back to the model.
            if not isinstance(parsed, dict):
                error_hint = (
                    f"Expected a JSON object with field 'chunk_ids'; "
                    f"got a {type(parsed).__name__} instead."
                )
                continue

            ids_raw = parsed.get("chunk_ids")
            if not isinstance(ids_raw, list):
                if "chunk_ids" not in parsed:
                    visible_keys = ", ".join(sorted(str(k) for k in parsed.keys())[:6]) or "<none>"
                    error_hint = (
                        "Missing required field 'chunk_ids'. Use that "
                        f"exact key. Your object had: {visible_keys}."
                    )
                else:
                    error_hint = (
                        "Field 'chunk_ids' must be a list of strings; "
                        f"got a {type(ids_raw).__name__} instead."
                    )
                continue

            out: list[str] = []
            seen: set = set()
            for cid in ids_raw:
                if not isinstance(cid, str):
                    continue
                cid = cid.strip()
                if cid in valid_set and cid not in seen:
                    out.append(cid)
                    seen.add(cid)

            if not out:
                # LLM returned a list but none of the ids are real.
                # Reshow the valid id set with concrete examples so
                # the model can pick from them on the next attempt.
                example_ids = sorted(valid_set)[:8]
                more = "" if len(valid_set) <= 8 else f" (and {len(valid_set) - 8} more)"
                error_hint = (
                    "None of the chunk_ids you provided matched the "
                    "retrieved set. Valid ids you can pick from: "
                    f"{example_ids}{more}. Return at least one of these."
                )
                continue

            # All validations passed.
            if attempt > 0:
                logger.info(
                    f"chunk-id selection succeeded on attempt {attempt + 1}/"
                    f"{max_attempts} after validation-retry"
                )
            return out

        logger.warning(
            f"chunk-id selection: all {max_attempts} attempts failed; "
            f"final error hint: {error_hint!r}"
        )
        return []

    def _reattribute_and_extract(
        self,
        answer: str,
        sources: list[dict[str, Any]],
        used_chunk_ids: list[str] | None = None,
    ) -> tuple:
        """Inject ``[cN]`` markers into the answer based on the set
        of chunk_ids the LLM (via JSON-mode follow-up) said it
        used, then build the citations list.

        Algorithm:
          1. Split the answer into sentences.
          2. For each chunk_id in ``used_chunk_ids``, run the
             cross-encoder reranker over (chunk_text, sentence)
             pairs and pick the sentence with the highest score —
             that's the sentence this chunk best supports.
          3. Insert ``[cN]`` immediately after the chosen sentence.
             If two chunks land on the same sentence, both markers
             stack at that boundary.
          4. Build the citations list with the best sentence FROM
             the chunk (used by the highlight panel as ``quote``).

        Falls back gracefully:
          * No reranker → attach all used_chunk_ids as a final
            citation block at the end of the answer.
          * No used_chunk_ids → uses top-2 reranker-scored chunks
            against the whole answer (so we never return uncited).
          * Empty answer or sources → return as-is.

        Returns ``(answer_with_markers, citations)`` — citations is
        a list of ``{chunk_id, quote}`` dicts.
        """
        if not answer or not sources:
            return answer, []

        # Belt-and-braces: the system prompt tells the LLM not to
        # emit ``[cN]``/``[N]`` markers itself, but small models
        # occasionally still do. Strip them BEFORE we inject — the
        # reranker chooses placement, so any pre-existing markers
        # would just produce double-citations on a single sentence.
        marker_pattern = re.compile(r"\[\s*[Cc]?\s*\d+\s*\]")
        answer = marker_pattern.sub("", answer)
        # Collapse any double-spaces left behind so the prose reads
        # cleanly when the corrected frame replaces the streamed text.
        answer = re.sub(r"  +", " ", answer)

        # Build candidate pool of (id, text).
        chunk_text_by_id = {
            s["chunk_id"]: s.get("chunk_text", "")
            for s in sources
            if s.get("chunk_id") and s.get("chunk_text", "").strip()
        }
        if not chunk_text_by_id:
            return answer, []

        # Split answer into sentences with their byte ranges. Markdown
        # bullets and headings count as their own "sentence" so list
        # answers attribute one citation per item rather than the
        # whole list collapsing onto one chunk.
        sentences = self._split_into_sentences(answer)
        if not sentences:
            return answer, []

        # Resolve the working set of chunk_ids. If the structured
        # call returned nothing, fall back to "top-2 chunks against
        # the whole answer" using the reranker so we never serve an
        # uncited answer.
        working_ids: list[str] = []
        if used_chunk_ids:
            for cid in used_chunk_ids:
                if cid in chunk_text_by_id:
                    working_ids.append(cid)

        if not working_ids:
            working_ids = self._fallback_top_chunks_for_answer(answer, chunk_text_by_id, top_n=2)

        if not working_ids:
            return answer, []

        # Reranker-driven sentence-to-chunk attribution. If the
        # reranker can't load, we degrade to "all citations land on
        # the final sentence" — still cited, just less precise.
        rk = self.reranker
        try:
            import numpy as np
        except ImportError:
            np = None

        if rk is None or np is None:
            tail_idx = len(sentences) - 1
            citations = [
                {
                    "chunk_id": cid,
                    "quote": (chunk_text_by_id[cid] or "")[:300].strip(),
                }
                for cid in working_ids
            ]
            answer_with_markers = self._inject_markers_into_sentences(
                answer, sentences, {tail_idx: working_ids}
            )
            return answer_with_markers, citations

        # Token budgets — same trick as before. Pre-truncate the
        # chunk text ONCE per query and reuse across every chunk_id
        # we resolve.
        max_total = max(64, self._get_reranker_max_tokens() - 8)
        sentence_budget = min(96, max_total // 4)
        chunk_budget = max(64, max_total - sentence_budget)
        sentence_truncated = [
            self._truncate_for_reranker(s["text"], sentence_budget) for s in sentences
        ]

        # sentence_idx -> [chunk_id, ...]
        attach_map: dict[int, list[str]] = {}
        citations: list[dict[str, Any]] = []

        for cid in working_ids:
            chunk_text = chunk_text_by_id.get(cid, "")
            if not chunk_text.strip():
                continue
            t_chunk = self._truncate_for_reranker(chunk_text, chunk_budget)
            try:
                pairs = [[t_chunk, s] for s in sentence_truncated]
                scores = rk.predict(pairs)
            except Exception as e:
                logger.warning(f"Reranker sentence-attribution failed for {cid}: {e}")
                continue
            best_idx = int(np.argmax(scores))
            attach_map.setdefault(best_idx, []).append(cid)
            citations.append(
                {
                    "chunk_id": cid,
                    "quote": self._find_best_sentence(sentences[best_idx]["text"], chunk_text),
                }
            )

        if not attach_map:
            return answer, []

        answer_with_markers = self._inject_markers_into_sentences(answer, sentences, attach_map)
        return answer_with_markers, citations

    @staticmethod
    def _normalize_for_match(text: str) -> str:
        """Lowercase + single-space text for fuzzy span matching."""
        return re.sub(r"\s+", " ", text.strip().lower())

    @staticmethod
    def _verbatim_match(
        sentence: str,
        chunk_text: str,
        min_words: int = 6,
        ratio: float = 0.90,
    ) -> str | None:
        """Return a source sentence verifying ``sentence`` with no
        model inference, else None.

        Instant hit when the normalized answer sentence contains (or
        is contained in) a normalized source sentence — covers exact
        quotes and lightly edited numbers/definitions. Otherwise a
        difflib ratio over source sentences, accepted at >= 0.90.
        Short sentences (< min_words) never match: they carry no
        verifiable claim and would only anchor noise.
        """
        if len(sentence.split()) < min_words:
            return None
        import difflib

        target = self._normalize_for_match(sentence)
        best_src = ""
        best_ratio = 0.0
        for raw in re.split(r"(?<=[.!?])\s+", chunk_text):
            src = raw.strip()
            if len(src.split()) < min_words:
                continue
            norm = self._normalize_for_match(src)
            if target in norm or norm in target:
                return src
            r = difflib.SequenceMatcher(None, target, norm).ratio()
            if r > best_ratio:
                best_ratio = r
                best_src = src
        return best_src if best_ratio >= ratio else None

    def _attribute_sentences_to_sources(
        self,
        answer: str,
        sources: list[dict[str, Any]],
        floor: float = 0.0,
        margin: float = 1.0,
    ) -> tuple:
        """Fast deterministic citation attribution — zero LLM calls.

        Per answer sentence, in order:
          1. Skip headings, short boilerplate, and refusal/disclaimer
             sentences (an honest "not in context" carries no claim
             to certify — stays uncited).
          2. Verbatim fast-path: an exact/near quote in a source chunk
             attaches instantly at score 1.0, no inference.
          3. Leftovers are scored against every chunk in ONE batched
             cross-encoder ``predict``; the best chunk attaches when
             it shows absolute certainty (score >= ``floor``) or
             distinctiveness (beats that sentence's mean chunk score
             by >= ``margin`` — scale-invariant, survives domain
             logit shift). An already-cited chunk re-attaches only
             when distinctly better than the best fresh alternative,
             so answers don't collapse onto one number.
        Unsupported sentences stay visibly uncited — never rewarded
        with a plausible-but-wrong reference.

        Returns ``(answer_with_markers, citations)`` — citations carry
        ``{chunk_id, quote, page, source, title, score, verified,
        attribution_method}``. ``score`` is always a plain float so
        the NDJSON frame stays JSON-serializable. Quotes diversify:
        sentences sharing one chunk get distinct passages, not the
        same line repeated.
        """
        if not answer or not sources:
            return answer, []

        # Same strip as _reattribute_and_extract: small models
        # sometimes emit markers themselves; the scorer chooses
        # placement, so pre-existing markers would double-cite.
        marker_pattern = re.compile(r"\[\s*[Cc]?\s*\d+\s*\]")
        answer = marker_pattern.sub("", answer)
        answer = re.sub(r"  +", " ", answer)

        chunk_text_by_id: dict[str, str] = {}
        meta_by_id: dict[str, dict[str, Any]] = {}
        for s in sources:
            cid = s.get("chunk_id")
            text = s.get("chunk_text", "")
            if cid and text.strip() and cid not in chunk_text_by_id:
                chunk_text_by_id[cid] = text
                meta_by_id[cid] = s
        if not chunk_text_by_id:
            return answer, []

        sentences = self._split_into_sentences(answer)
        if not sentences:
            return answer, []

        def _cite(
            cid: str,
            quote: str,
            score: float,
            method: str,
            verified: bool,
        ) -> dict[str, Any]:
            meta = meta_by_id.get(cid, {})
            return {
                "chunk_id": cid,
                "quote": quote,
                "page": meta.get("page"),
                "source": meta.get("source", ""),
                "title": meta.get("doc_title", ""),
                "score": float(score),
                "verified": verified,
                "attribution_method": method,
            }

        attach_map: dict[int, list[str]] = {}
        citations: list[dict[str, Any]] = []
        n_verbatim = 0
        pending: list[tuple] = []  # (sentence_idx, text)

        for idx, sent in enumerate(sentences):
            text = sent["text"].strip()
            if text.startswith("#") or len(text.split()) < 8:
                continue
            if _is_non_factual(text):
                continue
            hit_cid = ""
            hit_quote = ""
            for cid, ctext in chunk_text_by_id.items():
                quote = self._verbatim_match(text, ctext)
                if quote:
                    hit_cid, hit_quote = cid, quote
                    break
            if hit_cid:
                attach_map.setdefault(idx, []).append(hit_cid)
                citations.append(_cite(hit_cid, hit_quote, 1.0, "verbatim", True))
                n_verbatim += 1
            else:
                pending.append((idx, text))

        n_scored = 0
        rk_state = "unused"
        used_quotes: list[str] = []
        if pending:
            rk = self.reranker
            rk_state = "ok" if rk is not None else "missing"
            if rk is not None:
                try:
                    max_total = max(64, self._get_reranker_max_tokens() - 8)
                    sentence_budget = min(96, max_total // 4)
                    chunk_budget = max(64, max_total - sentence_budget)
                    pending_text = dict(pending)
                    trunc_sent = [
                        self._truncate_for_reranker(t, sentence_budget)
                        for t in pending_text.values()
                    ]
                    trunc_chunk = {
                        cid: self._truncate_for_reranker(ct, chunk_budget)
                        for cid, ct in chunk_text_by_id.items()
                    }
                    cids = list(chunk_text_by_id.keys())
                    order: list[tuple] = []
                    pairs: list[list] = []
                    for i, idx in enumerate(pending_text.keys()):
                        for cid in cids:
                            order.append((idx, cid))
                            pairs.append([trunc_chunk[cid], trunc_sent[i]])
                    scores = rk.predict(pairs)
                    scores_by_idx: dict[int, list] = {}
                    for (idx, cid), sc in zip(order, scores):
                        scores_by_idx.setdefault(idx, []).append((cid, float(sc)))
                    n_scored = len(scores_by_idx)
                    # Chunks already cited (verbatim hits included).
                    # Without diversity pressure every sentence
                    # independently picks the broadest chunk and the
                    # answer reads "1 1 1 1".
                    used_ids = {cid for ids in attach_map.values() for cid in ids}
                    for idx, scored in scores_by_idx.items():
                        ordered = sorted(scored, key=lambda t: t[1], reverse=True)
                        mean = sum(s for _, s in scored) / len(scored)
                        cid, sc_f = ordered[0]
                        if cid in used_ids:
                            fresh = next(
                                (t for t in ordered if t[0] not in used_ids),
                                None,
                            )
                            if fresh is not None and (sc_f - fresh[1]) < margin:
                                # Not distinctly better than the best
                                # fresh alternative — defer to it.
                                cid, sc_f = fresh
                        if sc_f >= floor or (sc_f - mean) >= margin:
                            attach_map.setdefault(idx, []).append(cid)
                            used_ids.add(cid)
                            quote = self._find_best_sentence(
                                pending_text[idx],
                                chunk_text_by_id[cid],
                                exclude=tuple(used_quotes),
                            )
                            used_quotes.append(quote)
                            citations.append(
                                _cite(
                                    cid,
                                    quote,
                                    sc_f,
                                    "cross_encoder",
                                    True,
                                )
                            )
                except Exception as e:
                    logger.warning(f"Fast citation scoring failed: {e}")

        logger.warning(
            "[CITATION DIAG] mode=fast sentences=%d verbatim=%d "
            "scored=%d cited=%d floor=%s margin=%s rk=%s",
            len(sentences),
            n_verbatim,
            n_scored,
            len(citations),
            floor,
            margin,
            rk_state,
        )
        answer_with_markers = self._inject_markers_into_sentences(answer, sentences, attach_map)
        return answer_with_markers, citations

    @staticmethod
    def _split_into_sentences(text: str) -> list[dict[str, Any]]:
        """Split ``text`` into sentence-like spans for citation
        attribution. Returns dicts with ``text``, ``start``, ``end``
        — char offsets into the original string, end-exclusive.

        Markdown awareness:
          * Each non-blank line is treated as its own unit.
          * Inside paragraph lines, sentence boundaries split on
            ``. ! ?`` followed by whitespace.
          * Bullet points and headings stay as one unit (don't try
            to sub-split a single list item).
          * Trailing whitespace and any markers we left in are
            tolerated — markers can land just after the visible
            text.
        """
        if not text:
            return []
        spans: list[dict[str, Any]] = []
        # Walk the text line by line, tracking absolute offsets.
        i = 0
        n = len(text)
        while i < n:
            j = text.find("\n", i)
            if j == -1:
                j = n
            line = text[i:j]
            stripped = line.strip()
            if not stripped:
                i = j + 1
                continue

            # Bullets/headings — keep as one span to preserve list
            # boundaries (citation lands right after the item).
            if re.match(r"^\s*(?:[-*+]\s|\d+[.)]\s|#{1,6}\s)", line):
                spans.append({"text": stripped, "start": i, "end": j})
                i = j + 1
                continue

            # Paragraph line — sub-split on sentence delimiters,
            # but never after abbreviations (species "L.", "et al.",
            # "Fig.", "sp." ...) — splitting there drops markers
            # mid-sentence ("L. [c1]from ...").
            local = 0
            for m in re.finditer(r"[.!?](?:\s+|$)", line):
                prefix = line[: m.start()]
                tok = prefix.split()[-1] if prefix.split() else ""
                if (len(tok) == 1 and tok.isupper()) or tok.lower() in {
                    "al",
                    "fig",
                    "eq",
                    "sp",
                    "spp",
                    "var",
                    "cf",
                    "eg",
                    "ie",
                    "e.g",
                    "i.e",
                    "vs",
                    "no",
                    "ref",
                    "etc",
                }:
                    continue
                end_local = m.end()
                seg = line[local:end_local].strip()
                if seg:
                    seg_start = i + local
                    seg_end = i + end_local
                    spans.append(
                        {
                            "text": seg,
                            "start": seg_start,
                            "end": seg_end,
                        }
                    )
                local = end_local
            if local < len(line):
                tail = line[local:].strip()
                if tail:
                    spans.append(
                        {
                            "text": tail,
                            "start": i + local,
                            "end": j,
                        }
                    )
            i = j + 1
        return spans

    @staticmethod
    def _inject_markers_into_sentences(
        answer: str,
        sentences: list[dict[str, Any]],
        attach_map: dict[int, list[str]],
    ) -> str:
        """Splice ``[cN]`` markers into ``answer`` at each sentence's
        end offset. Markers for the same sentence are concatenated
        in stable order. Edits are applied in REVERSE so earlier
        offsets remain valid as we splice.
        """
        if not attach_map:
            return answer
        edits: list[tuple] = []
        for idx, ids in attach_map.items():
            if idx < 0 or idx >= len(sentences):
                continue
            insert_pos = sentences[idx]["end"]
            marker_str = "".join(f"[{cid}]" for cid in ids)
            # Strip any trailing whitespace/punctuation already
            # present at insert_pos so the marker sits flush.
            edits.append((insert_pos, marker_str))
        if not edits:
            return answer
        out = answer
        for pos, marker in sorted(edits, key=lambda x: -x[0]):
            out = out[:pos] + marker + out[pos:]
        return out

    @staticmethod
    def _strip_to_body(text: str, title: str = "") -> str:
        """Strip context-header lines added at chunking time so what
        remains is the verbatim body that appears in the saved paper
        markdown.

        Handles every prefix shape the chunking code currently
        produces:
          - PyMuPDF:  ``"<doc_title> > <section>\\n\\n<body>"``
          - PyMuPDF:  ``"<section>\\n\\n<body>"`` (no doc_title)
          - PyMuPDF:  ``"## <section>\\n\\n<body>"``
          - Docling:  ``"<doc_title>\\n\\n<contextualized chunk>\\n\\n<body>"``
          - Docling:  ``"<doc_title>\\n\\n## <section>\\n\\n<body>"``

        Walks lines from the top peeling off anything that looks
        like a header (markdown heading, doc title, breadcrumb,
        short non-prose line). Stops at the first line that looks
        like body prose. The returned text is what
        ``MarkdownPreviewPanel.findFlexibleSpan`` will look for in
        the paper markdown — keeping it as a verbatim substring
        means the exact-match strategy succeeds and the highlight
        lands precisely.
        """
        if not text:
            return ""
        lines = text.splitlines()
        body_start = 0
        title_norm = title.strip() if title else ""

        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                # Blank line — keep walking; final lstrip cleans up.
                body_start = i + 1
                continue
            # Markdown heading line (#, ##, …)
            if re.match(r"^#{1,6}\s+\S", stripped):
                body_start = i + 1
                continue
            # Exact doc title alone
            if title_norm and stripped == title_norm:
                body_start = i + 1
                continue
            # Doc title plus breadcrumb continuation
            if title_norm and stripped.startswith(title_norm + " >"):
                body_start = i + 1
                continue
            # Generic breadcrumb-style header: contains " > ", short,
            # no terminal sentence punctuation. Body prose wouldn't
            # match this shape.
            if " > " in stripped and len(stripped) < 200 and not re.search(r"[.!?:]\s*$", stripped):
                body_start = i + 1
                continue
            # Otherwise — looks like body. Stop peeling.
            break

        body = "\n".join(lines[body_start:]).lstrip("\n").strip()
        # Defensive: if our walker stripped EVERYTHING (over-eager
        # heuristic on a short chunk), fall back to the original
        # text minus leading whitespace. Better fuzz than nothing.
        return body if body else text.lstrip()

    @staticmethod
    def _build_references_block(
        citations: list[dict[str, Any]],
        citable_sources: list[dict[str, Any]],
    ) -> str:
        """Compact ``References:`` block for normal chat — deduped per
        cited chunk in order of first appearance. Each line carries the
        section (when the chunk has one), title, and page so the reader
        sees *where* the claim came from without opening every badge.
        Falls back gracefully when section/page are absent (tables,
        docling chunks).

        Returns ``""`` when there are no citations — no empty heading.
        """
        if not citations:
            return ""
        source_by_id = {s["chunk_id"]: s for s in citable_sources if s.get("chunk_id")}
        seen: set = set()
        order: list[str] = []
        for c in citations:
            cid = c.get("chunk_id")
            if cid and cid not in seen:
                seen.add(cid)
                order.append(cid)
        if not order:
            return ""
        lines = ["\n\n---\n\n**References**\n"]
        for idx, cid in enumerate(order, 1):
            src = source_by_id.get(cid, {})
            # Prefer the citation's own enriched page/title when
            # present (fast path carries them), else the source row.
            # ``section`` lives only on the source row.
            cite = next((x for x in citations if x.get("chunk_id") == cid), {})
            section = (src.get("section") or "").strip()
            title = (cite.get("title") or src.get("doc_title") or src.get("source") or "").strip()
            page = cite.get("page") if cite.get("page") is not None else src.get("page")
            # One-line reference: section prominent, then title + page.
            parts: list[str] = []
            if section:
                parts.append(section)
            if title:
                # Avoid repeating the filename when the section already
                # equals the title (rare, but keeps the line short).
                if not section or title.lower() != section.lower():
                    parts.append(title)
            display = ""
            if parts:
                display = " — ".join(parts)
                if page:
                    display += f" (p. {page})"
                fname = (src.get("source") or cite.get("source") or "").strip()
                if fname and fname not in display:
                    display += f" — {fname}"
            else:
                display = cid
                if page:
                    display += f" (p. {page})"
            # Clickable: References number and text both open the
            # chunk's markdown preview (same handler as inline badges).
            ref = f"{idx}. [{display}](#cite-{cid})"
            lines.append(ref)
        return "\n".join(lines)

    async def _extract_citations(
        self,
        answer: str,
        chunks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Pass 2 of the citation pipeline — extract verbatim quotes
        per cited chunk_id from the streamed answer.

        Pipeline:
          1. Regex-find all ``[hex_id]`` markers actually present in
             the answer (the LLM may have skipped some chunks).
          2. Filter ``chunks`` down to just the cited ones.
          3. Ask the LLM (JSON mode) to map each cited chunk_id to
             a verbatim quote from that chunk's text.
          4. Validate the response with the ``Citations`` Pydantic
             schema, then drop any citation whose ``chunk_id`` isn't
             in the cited set OR whose ``quote`` doesn't fuzzy-match
             the chunk text (>=80% via difflib SequenceMatcher).
          5. Return validated citations as plain dicts.

        Returns ``[]`` on any failure — citations are nice-to-have,
        the streamed answer is the primary deliverable.
        """
        from backend.src.domain.schemas import Citations as _CitationsSchema

        # 1. Find chunk_ids actually mentioned in the answer.
        # Tolerates every marker variant LLMs commonly emit:
        # ``[c1]``, ``[1]``, ``[C1]``, ``[ c1]``, ``[ 1 ]``, etc.
        # All normalize to ``cN`` for downstream lookup.
        # Bounds-checked below by intersecting with retrieved
        # chunk_ids so bare numeric brackets quoted from paper
        # body text don't false-positive.
        marker_pattern = re.compile(r"\[\s*[Cc]?\s*(\d+)\s*\]")
        cited_ids = {f"c{num}" for num in marker_pattern.findall(answer)}
        if not cited_ids:
            return []

        # 2. Filter chunks down to cited ones.
        cited_chunks = [c for c in chunks if c.get("chunk_id") in cited_ids]
        if not cited_chunks:
            return []

        # 3. Build the JSON-mode prompt. We hand the LLM only the
        # chunks it actually cited so the prompt stays small and
        # fits even Ollama's modest 4-8K default context windows.
        chunks_block = "\n\n".join(
            f"[{c['chunk_id']}]\n{c.get('chunk_text', '')[:2000]}" for c in cited_chunks
        )

        prompt = (
            "You are a citation extractor. Given an ANSWER and the source "
            "CHUNKS it cites, return a JSON object listing the verbatim "
            "quote from each chunk that supports the answer's claim about "
            "that chunk.\n\n"
            "Rules:\n"
            "- Output ONLY a JSON object matching: "
            '{"citations": [{"chunk_id": "...", "quote": "..."}]}\n'
            "- Each `quote` MUST be a verbatim substring (or near-verbatim "
            "phrase) from the matching chunk's text.\n"
            "- Maximum 300 characters per quote.\n"
            "- One citation entry per cited chunk_id.\n"
            "- Do NOT include chunk_ids that do not appear in CHUNKS.\n"
            "- Do NOT include any prose or explanation outside the JSON.\n\n"
            f"ANSWER:\n{answer}\n\n"
            f"CHUNKS:\n{chunks_block}\n\n"
            "JSON:"
        )

        try:
            response = await self._invoke_llm(
                prompt=prompt,
                max_retries=1,
                timeout_seconds=30.0,
                response_format={"type": "json_object"},
            )
        except Exception as e:
            logger.warning(f"Citation pass-2 LLM call failed: {e}")
            return []

        raw = (response.content or "").strip()
        if not raw:
            return []

        # 4. Pydantic-validate. Strip code fences a few small models
        # add even in JSON mode.
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE)
        try:
            parsed = _CitationsSchema.model_validate_json(raw)
        except Exception as e:
            logger.warning(f"Citation JSON failed Pydantic validation: {e}")
            return []

        # 5. Validate each citation: chunk_id must be cited, quote
        # must fuzzy-match the chunk text.
        chunk_text_by_id = {c["chunk_id"]: (c.get("chunk_text") or "") for c in cited_chunks}
        validated: list[dict[str, Any]] = []
        seen_ids: set = set()
        for cit in parsed.citations:
            cid = cit.chunk_id.strip()
            quote = cit.quote.strip()
            if not cid or not quote:
                continue
            if cid not in cited_ids:
                continue
            if cid in seen_ids:
                continue
            chunk_text = chunk_text_by_id.get(cid, "")
            if not _quote_matches_chunk(quote, chunk_text):
                logger.debug(f"Dropping hallucinated citation for {cid}: quote not in chunk text")
                continue
            seen_ids.add(cid)
            # ``verified=True`` is redundant given the chunk_id +
            # fuzzy-match checks above already passed; we set it
            # explicitly so the frontend (or future analytics) can
            # filter on it without relying on list membership alone.
            validated.append({"chunk_id": cid, "quote": quote, "verified": True})

        # Marker completeness check: track which [chunk_id] markers
        # in the answer lacked a validated quote. Unsubstantiated
        # markers trigger warnings to detect prompt drift.
        unverified_ids = cited_ids - seen_ids
        total_markers = len(cited_ids)
        if unverified_ids:
            logger.warning(
                "[CITATION DIAG] completeness=%s/%s markers verified; unverified=%s",
                total_markers - len(unverified_ids),
                total_markers,
                sorted(unverified_ids),
            )
        else:
            logger.warning(
                "[CITATION DIAG] completeness=%s/%s markers verified",
                total_markers,
                total_markers,
            )

        return validated


def _is_chrome_sentence(sentence: str) -> bool:
    """True for publisher running heads/footers — journal name bars,
    DOI/URL lines, copyright notices, all-caps headings. Quotes must
    never anchor on these when body sentences score low."""
    s = sentence.strip()
    if re.search(r"https?://|doi\.org|©|all rights reserved", s, re.IGNORECASE):
        return True
    letters = [c for c in s if c.isalpha()]
    return len(s) >= 10 and len(letters) >= 2 and s == s.upper()


_NON_FACTUAL_FRAGMENTS = (
    "does not mention",
    "do not mention",
    "does not contain",
    "do not contain",
    "no information",
    "not enough information",
    "insufficient information",
    "cannot answer",
    "can't answer",
    "unable to answer",
    "don't know",
    "do not know",
    "not provided",
    "not in the context",
    "unclear from",
    "cannot determine",
    "can't determine",
    "is not available",
)


def _is_non_factual(sentence: str) -> bool:
    """True when the sentence abstains or disclaims (no verifiable
    claim to cite)."""
    low = sentence.lower()
    return any(frag in low for frag in _NON_FACTUAL_FRAGMENTS)
