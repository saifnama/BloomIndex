/**
 * CitationPreviewDrawer — side drawer that opens when the user clicks
 * a citation superscript in an assistant answer.
 *
 * Behavior:
 *   - Fetches ``/api/chat/files/<source>/markdown`` (utf-8 markdown
 *     extracted at ingest time; the backend lazy-regenerates it for
 *     papers uploaded before this feature shipped).
 *   - Locates the cited chunk's text inside the markdown and wraps
 *     it in ``<mark class="chunk-highlight">``. If the verbatim quote
 *     from Pass-2 citation extraction is also present inside that
 *     chunk, wraps just the quote in ``<mark class="quote-highlight">``
 *     (bright yellow, bold).
 *   - Scrolls the chunk-highlight into view on mount.
 *
 * No new dependencies — uses ``react-markdown`` already in the bundle.
 */

import { type FC, useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeRaw from 'rehype-raw';
import { X } from '@phosphor-icons/react';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Skeleton } from '@/components/ui/skeleton';
import type { Citation, RagSource } from './assistant/chatRuntimeAdapter';

const remarkPlugins = [remarkGfm];
const rehypePlugins = [rehypeRaw];

interface CitationPreviewDrawerProps {
  source: RagSource;
  citation?: Citation;
  /** Bumped by the parent on every citation click. Used as React
   * ``key`` on the inner markdown container so the same click
   * (or repeat-click on the same source) restarts the CSS flash
   * animation by force-remounting the highlight elements. */
  triggerKey: number;
  onClose: () => void;
}

/** Find the best in-text occurrence of ``needle`` inside ``haystack``
 * after whitespace+case normalization. Returns the [start, end)
 * indices into the original haystack, or null if no good match
 * exists. Cascades through three strategies of increasing
 * looseness so reassembled / lightly-paraphrased chunks still
 * anchor on the right region: exact match → multi-slice match →
 * n-gram density cluster (the strongest fallback, picks the spot
 * where the most distinctive substrings of the needle co-occur). */
function findFlexibleSpan(
  haystack: string,
  needle: string,
): [number, number] | null {
  if (!needle || !haystack) return null;
  const normalizedNeedle = needle.replace(/\s+/g, ' ').trim();
  if (normalizedNeedle.length < 8) return null;

  // Build a parallel haystack where consecutive whitespace becomes a
  // single space, but track original indices.
  const indexMap: number[] = [];
  let normalized = '';
  let prevWasSpace = false;
  for (let i = 0; i < haystack.length; i += 1) {
    const ch = haystack[i];
    if (/\s/.test(ch)) {
      if (!prevWasSpace) {
        normalized += ' ';
        indexMap.push(i);
        prevWasSpace = true;
      }
    } else {
      normalized += ch;
      indexMap.push(i);
      prevWasSpace = false;
    }
  }

  const lowerNorm = normalized.toLowerCase();
  const lowerNeedle = normalizedNeedle.toLowerCase();

  // Strategy 1: exact substring match after whitespace collapse.
  const exact = lowerNorm.indexOf(lowerNeedle);
  if (exact !== -1) {
    const start = indexMap[exact];
    const endNormIdx = exact + lowerNeedle.length - 1;
    const end = indexMap[Math.min(endNormIdx, indexMap.length - 1)] + 1;
    return [start, end];
  }

  // Strategy 2: multi-offset slice match — pick the longest
  // matching slice and use its position. Catches contiguous-tail
  // matches when the leading text was paraphrased.
  const sliceLength = 60;
  const candidateOffsets = [
    0,
    Math.floor(lowerNeedle.length * 0.25),
    Math.floor(lowerNeedle.length * 0.5),
    Math.floor(lowerNeedle.length * 0.75),
    Math.max(0, lowerNeedle.length - sliceLength),
  ];
  let bestSliceStart = -1;
  let bestSliceOffset = -1;
  let bestSliceLen = 0;
  for (const offset of candidateOffsets) {
    const slice = lowerNeedle.slice(offset, offset + sliceLength);
    if (slice.length < 24) continue;
    const found = lowerNorm.indexOf(slice);
    if (found !== -1 && slice.length > bestSliceLen) {
      bestSliceStart = found;
      bestSliceOffset = offset;
      bestSliceLen = slice.length;
    }
  }
  if (bestSliceStart !== -1) {
    const startInOrig = indexMap[bestSliceStart];
    const expandedStart = Math.max(0, startInOrig - bestSliceOffset);
    const expandedEnd = Math.min(
      haystack.length,
      expandedStart + needle.length,
    );
    return [expandedStart, expandedEnd];
  }

  // Strategy 3: n-gram density cluster.
  //
  // Robust fallback for non-contiguous or lightly-paraphrased
  // chunks — Docling HybridChunker reassembles content across
  // structural boundaries, so the chunk's bytes exist in the
  // markdown but not as a single substring. Build a fingerprint of
  // the chunk as 8-char windows, locate each window in the
  // markdown, then find the densest cluster of those positions
  // (the spot where the most distinctive parts of the chunk
  // co-occur). This is what diff and content-fingerprinting tools
  // use; it gracefully degrades from "exact match" toward "best
  // approximate region" without ever returning a confidently-wrong
  // position the way leading-N-chars matching does.
  const N = 8;
  const STEP = 4; // sparser sampling — we don't need every n-gram
  const grams = new Set<string>();
  for (let i = 0; i + N <= lowerNeedle.length; i += STEP) {
    const g = lowerNeedle.slice(i, i + N);
    // Only keep grams with at least one alphanumeric char so we
    // don't anchor on whitespace or punctuation runs.
    if (/[a-z0-9]/.test(g)) grams.add(g);
  }
  if (grams.size < 3) return null;

  // Locate each n-gram. Cap total positions to keep the worst
  // case bounded for large papers.
  const POSITION_CAP = 20000;
  const positionsNorm: number[] = [];
  for (const g of grams) {
    let from = 0;
    let cycles = 0;
    while (cycles < 200) {
      const idx = lowerNorm.indexOf(g, from);
      if (idx === -1) break;
      positionsNorm.push(idx);
      from = idx + 1;
      cycles += 1;
      if (positionsNorm.length >= POSITION_CAP) break;
    }
    if (positionsNorm.length >= POSITION_CAP) break;
  }
  if (positionsNorm.length < 2) return null;

  positionsNorm.sort((a, b) => a - b);
  const windowSize = lowerNeedle.length;
  let bestCount = 0;
  let bestStartNorm = positionsNorm[0];
  let left = 0;
  for (let right = 0; right < positionsNorm.length; right += 1) {
    while (positionsNorm[right] - positionsNorm[left] > windowSize) {
      left += 1;
    }
    const count = right - left + 1;
    if (count > bestCount) {
      bestCount = count;
      bestStartNorm = positionsNorm[left];
    }
  }

  // Require multiple distinctive matches to claim a hit — a single
  // generic n-gram match isn't enough to anchor on.
  if (bestCount < 3) return null;

  const startInOrig = indexMap[bestStartNorm];
  return [startInOrig, Math.min(haystack.length, startInOrig + needle.length)];
}

/** Restrict ``markdown`` to the region of 1-based ``page`` (delimited
 * by ``<!-- Page N -->`` markers). Fuzzy matching inside one page
 * cannot lock onto a duplicate passage (abstract-vs-body repeats
 * are the most common wrong-hit). Returns the full markdown when
 * the page markers are absent. */
function scopeToPage(markdown: string, page: number): string {
  const open = `<!-- Page ${page} -->`;
  const start = markdown.indexOf(open);
  if (start === -1) return markdown;
  const bodyStart = start + open.length;
  const next = markdown.indexOf('<!-- Page ', bodyStart);
  return next === -1 ? markdown.slice(start) : markdown.slice(start, next);
}

/** Splice a single ``mark`` tag into the markdown anchored on the
 * most-specific match we can find. Strategy cascade, ordered by
 * accuracy not by latency (all strategies are fast):
 *   0. ``body_start``/``body_end`` recorded at INGEST time → byte-
 *      exact slice of the markdown — but ONLY when the slice still
 *      contains the chunk/quote text (content-validated). Stale
 *      offsets from a re-extracted markdown fall through to fuzzy
 *      instead of highlighting the wrong span with confidence.
 *   1. If no offsets present (legacy chunk OR ingest-time substring
 *      search failed) and Pass 2 gave us a ``quote`` that appears
 *      in the markdown, highlight just the quote.
 *   2. If still no anchor, fuzzy-match the full chunk text via
 *      findFlexibleSpan's three-step cascade (exact → multi-slice
 *      → n-gram density).
 *   Strategies 1–2 search inside the cited page when known, so a
 *   repeated passage on another page can't steal the highlight.
 *   3. Last resort, render unhighlighted. */
function highlightInMarkdown(
  markdown: string,
  source: RagSource,
  quote?: string,
  page?: number | null,
  section?: string,
): { highlighted: string; chunkAnchorId: string | null } {
  const anchorId = 'pq-citation-anchor';
  const chunkText = source.chunk_text;
  const norm = (t: string) => t.replace(/\s+/g, ' ').trim().toLowerCase();
  // Scope to page first, then to section heading when available —
  // a heading's position is the cheapest section anchor and keeps
  // fuzzy search from locking onto the same passage on another page.
  let scoped =
    typeof page === 'number' && page > 0
      ? scopeToPage(markdown, page)
      : markdown;
  if (section && section.trim().length >= 4) {
    const secNorm = norm(section);
    // Find a markdown heading that contains the section text.
    const headingRe = new RegExp(
      `^#{1,6}\\s+.*${secNorm.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}.*$`,
      'im',
    );
    const m = headingRe.exec(scoped);
    if (m && m.index !== undefined) {
      // Keep from heading to next page break / end — the badge now
      // lands in its section, not just its page.
      const nextPage = scoped.indexOf('<!-- Page ', m.index + m[0].length);
      scoped =
        nextPage === -1
          ? scoped.slice(m.index)
          : scoped.slice(m.index, nextPage);
    }
  }
  // Offsets into ``scoped`` when scoping is active, so every slice
  // below still indexes the string it searches.
  const haystack = scoped;
  const haystackOffset = markdown.indexOf(haystack);

  // Strategy 0 — exact offset slice from ingest-time metadata.
  // Only honor offsets that look sane against the current markdown
  // (defensive in case the markdown was re-extracted with a
  // different parser since indexing) AND whose content still
  // matches — otherwise fall through to fuzzy search.
  const bs = source.body_start;
  const be = source.body_end;
  if (
    typeof bs === 'number' &&
    typeof be === 'number' &&
    bs >= 0 &&
    be > bs &&
    be <= markdown.length
  ) {
    const sliceNorm = norm(markdown.slice(bs, be));
    const probe =
      quote && quote.trim().length >= 8 ? quote : chunkText.slice(0, 120);
    if (probe && sliceNorm.includes(norm(probe).slice(0, 60))) {
      const before = markdown.slice(0, bs);
      let chunk = markdown.slice(bs, be);
      const after = markdown.slice(be);

      // Inside the offset-bounded chunk, brighten the verbatim quote
      // if Pass 2 returned one and it's actually present here.
      if (quote && quote.trim().length >= 8) {
        const quoteSpan = findFlexibleSpan(chunk, quote);
        if (quoteSpan) {
          const [qs, qe] = quoteSpan;
          chunk =
            chunk.slice(0, qs) +
            `<mark class="quote-highlight">${chunk.slice(qs, qe)}</mark>` +
            chunk.slice(qe);
        }
      }

      const wrapped = `<mark class="chunk-highlight" id="${anchorId}">${chunk}</mark>`;
      return { highlighted: before + wrapped + after, chunkAnchorId: anchorId };
    }
  }

  // Strategy 1 — quote-only fuzzy match (Pass 2 verbatim),
  // scoped to the cited page when known.
  if (quote && quote.trim().length >= 8) {
    const quoteSpan = findFlexibleSpan(haystack, quote);
    if (quoteSpan) {
      const [qStart, qEnd] = [
        quoteSpan[0] + haystackOffset,
        quoteSpan[1] + haystackOffset,
      ];
      const before = markdown.slice(0, qStart);
      const matched = markdown.slice(qStart, qEnd);
      const after = markdown.slice(qEnd);
      const wrapped = `<mark class="quote-highlight" id="${anchorId}">${matched}</mark>`;
      return { highlighted: before + wrapped + after, chunkAnchorId: anchorId };
    }
  }

  // Strategy 2 — chunk-text fuzzy match, same page scoping.
  const chunkSpan = findFlexibleSpan(haystack, chunkText);
  if (chunkSpan) {
    const [cStart, cEnd] = [
      chunkSpan[0] + haystackOffset,
      chunkSpan[1] + haystackOffset,
    ];
    const before = markdown.slice(0, cStart);
    const matched = markdown.slice(cStart, cEnd);
    const after = markdown.slice(cEnd);
    const wrapped = `<mark class="chunk-highlight" id="${anchorId}">${matched}</mark>`;
    return { highlighted: before + wrapped + after, chunkAnchorId: anchorId };
  }

  // Strategy 3 — unhighlighted. Panel still useful as a navigator.
  return { highlighted: markdown, chunkAnchorId: null };
}

export const CitationPreviewDrawer: FC<CitationPreviewDrawerProps> = ({
  source,
  citation,
  triggerKey,
  onClose,
}) => {
  const [markdown, setMarkdown] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const containerRef = useRef<HTMLDivElement | null>(null);

  // Re-fetch every time the source filename changes. The loading/error
  // resets are fetch lifecycle (with cancellation guard), not derived state
  // (per-call disable below).
  useEffect(() => {
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- fetch lifecycle
    setLoading(true);
    setError(null);
    setMarkdown(null);

    const url = `/api/chat/files/${encodeURIComponent(source.source)}/markdown`;
    fetch(url, { credentials: 'include' })
      .then(async (res) => {
        if (!res.ok) {
          throw new Error(`Failed to load markdown (${res.status})`);
        }
        return res.json() as Promise<{ markdown: string }>;
      })
      .then((data) => {
        if (cancelled) return;
        setMarkdown(data.markdown);
        setLoading(false);
      })
      .catch((err: Error) => {
        if (cancelled) return;
        setError(err.message ?? 'Failed to load markdown.');
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [source.source]);

  const { highlighted, chunkAnchorId } = useMemo(() => {
    if (!markdown) return { highlighted: '', chunkAnchorId: null };
    return highlightInMarkdown(
      markdown,
      source,
      citation?.quote,
      citation?.page ?? source.page,
      source.section,
    );
  }, [markdown, source, citation?.quote, citation?.page, source.section]);

  // Scroll the highlighted chunk into view once the markdown renders.
  useEffect(() => {
    if (!chunkAnchorId) return;
    const t = window.setTimeout(() => {
      const el = containerRef.current?.querySelector(`#${chunkAnchorId}`);
      if (el) {
        (el as HTMLElement).scrollIntoView({
          behavior: 'smooth',
          block: 'center',
        });
      }
    }, 50);
    return () => window.clearTimeout(t);
  }, [highlighted, chunkAnchorId]);

  return (
    <aside className="w-[min(32rem,42vw)] min-w-[22rem] border-l border-surface-c bg-background flex flex-col">
      <div className="flex items-center justify-between gap-2 px-4 py-3 border-b border-surface-c">
        <div className="min-w-0">
          <h3
            className="text-sm font-semibold text-on-surface truncate"
            title={citation?.title || source.source}
          >
            {citation?.title || source.source}
          </h3>
          {(source.section || citation?.page || source.page) && (
            <p className="text-xs text-base-content/60 truncate">
              {source.section && (
                <span className="font-medium text-on-surface/70">
                  {source.section}
                </span>
              )}
              {source.section && (citation?.page || source.page) ? ' · ' : ''}
              {citation?.page || source.page
                ? `p. ${citation?.page ?? source.page}`
                : ''}
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={onClose}
          className="p-1 text-black hover:text-black hover:opacity-75 transition-opacity outline-none border-0 bg-transparent flex items-center justify-center cursor-pointer"
          title="Close"
          aria-label="Close"
        >
          <X size={18} weight="bold" className="text-black" />
        </button>
      </div>

      {citation && (
        <div className="mx-5 mt-4 rounded-xl border border-[#fbcfe8] bg-[#ffecf6]/60 px-4 py-3 shrink-0">
          <p className="m-0 text-[13.5px] leading-relaxed text-slate-800 italic">
            &ldquo;{citation.quote}&rdquo;
          </p>
        </div>
      )}

      <div
        ref={containerRef}
        className="flex-1 overflow-y-auto px-5 py-4 text-sm leading-relaxed chat-scrollbar"
      >
        {loading && (
          <div className="space-y-3" aria-label="Loading">
            <Skeleton className="h-6 w-3/4" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-5/6" />
            <Skeleton className="h-4 w-4/6" />
            <Skeleton className="h-32 w-full mt-4" />
            <Skeleton className="h-4 w-full mt-4" />
            <Skeleton className="h-4 w-3/4" />
          </div>
        )}
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        {!loading && !error && markdown && (
          // ``key`` includes triggerKey so each citation click force-
          // remounts the rendered markdown — the new <mark> elements
          // re-trigger their CSS keyframe animation, producing the
          // brief yellow flash the user sees on click.
          <div
            key={triggerKey}
            className="prose prose-sm max-w-none prose-headings:mt-4 prose-headings:mb-2"
          >
            <ReactMarkdown
              skipHtml={false}
              remarkPlugins={remarkPlugins}
              rehypePlugins={rehypePlugins}
              components={{
                mark: ({ children, className, id }) => (
                  <mark className={className} id={id}>
                    {children}
                  </mark>
                ),
                table: ({ children }) => (
                  <div className="my-3 w-full overflow-x-auto rounded-lg border border-slate-200">
                    <table className="w-full border-collapse text-xs text-left text-slate-800">
                      {children}
                    </table>
                  </div>
                ),
                thead: ({ children }) => (
                  <thead className="bg-slate-50 border-b border-slate-200 text-slate-900 font-semibold">
                    {children}
                  </thead>
                ),
                tbody: ({ children }) => (
                  <tbody className="divide-y divide-slate-200/70 bg-white">
                    {children}
                  </tbody>
                ),
                tr: ({ children }) => (
                  <tr className="transition-colors hover:bg-slate-50/50">
                    {children}
                  </tr>
                ),
                th: ({ children }) => (
                  <th className="px-2.5 py-1.5 font-semibold text-slate-900 border-r border-slate-200 last:border-r-0">
                    {children}
                  </th>
                ),
                td: ({ children }) => (
                  <td className="px-2.5 py-1.5 text-slate-800 border-r border-slate-200/70 last:border-r-0 align-top leading-relaxed">
                    {children}
                  </td>
                ),
                sub: ({ children }) => (
                  <sub className="text-[75%] leading-none align-sub">
                    {children}
                  </sub>
                ),
                sup: ({ children }) => (
                  <sup className="text-[75%] leading-none align-super">
                    {children}
                  </sup>
                ),
              }}
            >
              {highlighted}
            </ReactMarkdown>
          </div>
        )}
      </div>
    </aside>
  );
};

export default CitationPreviewDrawer;
