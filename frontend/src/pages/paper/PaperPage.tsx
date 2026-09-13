import React, { useState, useEffect, useRef } from 'react';
import { useNavigate, getRouteApi } from '@tanstack/react-router';
import { ArrowLeft, SpinnerGap } from '@phosphor-icons/react';
import PaperViewer from '@/features/reader/PaperViewer';
import { doiApi, nerApi, paperApi, dbApi } from '../../lib/api/papers';
import { extractErrorDetail } from '../../lib/api/client';
import type { PaperData, Entity, TocItem } from '../../types';

const route = getRouteApi('/paper/$doi');

const PaperPage: React.FC = () => {
  const { doi } = route.useParams();
  const src = route.useSearch({ select: (s) => s.src });
  const navigate = useNavigate();
  const searchSource = src ?? '';
  const [paperData, setPaperData] = useState<PaperData | null>(null);
  const [entities, setEntities] = useState<Entity[]>([]);
  const [isExtracted, setIsExtracted] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isExtracting, setIsExtracting] = useState(false);
  const [extractionError, setExtractionError] = useState<string | null>(null);
  const [fallbackSource, setFallbackSource] = useState<{
    source: string;
    url: string;
  } | null>(null);
  const [fallbackLoading, setFallbackLoading] = useState(false);
  const [pdfActionError, setPdfActionError] = useState<string | null>(null);
  const [isDownloadingPdf, setIsDownloadingPdf] = useState(false);
  // "Done" confirmation — shown green for 20s after the download succeeds.
  const [downloadDone, setDownloadDone] = useState(false);
  const doiRef = useRef(doi);
  // Mirror the current DOI for async continuations (download started for
  // paper A must not touch paper B's UI). Assigned in an effect so the
  // ref is never written during render.
  useEffect(() => {
    doiRef.current = doi;
  }, [doi]);
  const doneTimer = useRef<number | undefined>(undefined);

  const flashDownloadDone = () => {
    setDownloadDone(true);
    if (doneTimer.current !== undefined) window.clearTimeout(doneTimer.current);
    doneTimer.current = window.setTimeout(() => setDownloadDone(false), 20000);
  };

  // Unmount: never leave a revert timer firing into a dead component.
  useEffect(() => {
    return () => {
      if (doneTimer.current !== undefined)
        window.clearTimeout(doneTimer.current);
    };
  }, []);

  // Paper switch: drop stale download state so paper B never shows
  // paper A's leftovers. Render-time adjustment (same commit, no extra
  // pass): an in-flight download still finishes for its own paper (it
  // closed over its identifier) but no longer touches the UI.
  const [prevDoi, setPrevDoi] = useState(doi);
  if (prevDoi !== doi) {
    setPrevDoi(doi);
    setPdfActionError(null);
    setIsDownloadingPdf(false);
    setDownloadDone(false);
  }

  const isExplicitDoi = (value: string) => {
    const trimmed = value.trim();
    return (
      /^10\.\d{4,}/i.test(trimmed) ||
      /^https?:\/\/(dx\.)?doi\.org\//i.test(trimmed) ||
      /^doi:/i.test(trimmed)
    );
  };

  const normalizeDoi = (value: string): string => {
    const trimmed = value.trim();
    // Extract DOI from full URLs: https://doi.org/10.xxxx/xxx or https://dx.doi.org/10.xxxx/xxx
    const urlMatch = trimmed.match(/^https?:\/\/(?:dx\.)?doi\.org\/(.+)$/i);
    if (urlMatch) return urlMatch[1];
    // Strip doi: prefix
    if (/^doi:/i.test(trimmed)) return trimmed.substring(4).trim();
    return trimmed;
  };

  const getLookupIdentifier = () => {
    const raw = doi?.trim();
    if (!raw) return undefined;

    if (raw.toUpperCase().startsWith('PMC')) {
      return {
        type: 'pmcid' as const,
        value: raw.toUpperCase(),
        href: `https://pmc.ncbi.nlm.nih.gov/articles/${raw.toUpperCase()}/`,
      };
    }

    if (/^\d+$/.test(raw)) {
      return {
        type: 'pmid' as const,
        value: raw,
        href: `https://pubmed.ncbi.nlm.nih.gov/${raw}/`,
      };
    }

    if (isExplicitDoi(raw)) {
      const normalized = normalizeDoi(raw);
      return {
        type: 'doi' as const,
        value: normalized,
        href: `https://doi.org/${normalized}`,
      };
    }

    return undefined;
  };

  const getPaperIdentifier = () => {
    const lookupIdentifier = getLookupIdentifier();
    if (lookupIdentifier) {
      return lookupIdentifier;
    }

    const resolvedDoi = paperData?.doi?.trim();
    if (resolvedDoi && isExplicitDoi(resolvedDoi)) {
      return {
        type: 'doi' as const,
        value: resolvedDoi,
        href: `https://doi.org/${resolvedDoi}`,
      };
    }

    const resolvedPmcid = paperData?.pmcid?.trim();
    if (resolvedPmcid) {
      return {
        type: 'pmcid' as const,
        value: resolvedPmcid,
        href: `https://pmc.ncbi.nlm.nih.gov/articles/${resolvedPmcid}/`,
      };
    }

    if (doi?.trim().startsWith('PMC')) {
      return {
        type: 'pmcid' as const,
        value: doi.trim(),
        href: `https://pmc.ncbi.nlm.nih.gov/articles/${doi.trim()}/`,
      };
    }

    if (doi && /^\d+$/.test(doi.trim())) {
      return {
        type: 'pmid' as const,
        value: doi.trim(),
        href: `https://pubmed.ncbi.nlm.nih.gov/${doi.trim()}/`,
      };
    }

    return undefined;
  };

  useEffect(() => {
    const fetchPaper = async () => {
      if (!doi) return;

      setIsLoading(true);
      setError(null);

      try {
        const dataPromise = nerApi.analysePaper(doi, false, searchSource);
        const dbEntitiesPromise = dbApi
          .getPaperEntities(doi)
          .catch(() => ({ entities: [] }));

        const [data, dbData] = await Promise.all([
          dataPromise,
          dbEntitiesPromise,
        ]);

        if (!data || 'error' in data) {
          setError(data.error || 'Paper not found');
          return;
        }

        setPaperData(data);

        if (data.entities && data.entities.length > 0) {
          setEntities(data.entities);
          setIsExtracted(true);
        } else if (dbData && dbData.entities && dbData.entities.length > 0) {
          setEntities(dbData.entities);
          setIsExtracted(true);
        }

        // Track if this came from a fallback source
        if (data.fallback_source) {
          setFallbackSource({
            source: data.fallback_source,
            url: data.fallback_url || '',
          });
        }
      } catch (err) {
        console.error('[PaperPage] Failed to fetch paper:', err);
        if (!isExplicitDoi(doi)) {
          const detailMsg = await extractErrorDetail(
            err,
            'Failed to load paper from the primary source.',
          );
          setError(detailMsg);
          return;
        }

        setFallbackLoading(true);
        try {
          const data = await doiApi.getAbstract(doi);
          setFallbackLoading(false);
          if (data?.abstract) {
            setPaperData({
              doi: data.doi || doi,
              mode: 'abstract' as const,
              title: data.title || '',
              html: `<section id="section-0"><h2>Abstract</h2><p>${data.abstract}</p></section>`,
              sections: [
                { title: 'Abstract', content: data.abstract, headings: [] },
              ],
              references: {},
              pmcid: '',
              authors: data.authors,
              year: data.year,
              isOpenAccess: (data as { isOpenAccess?: boolean }).isOpenAccess,
            } as PaperData);
            setFallbackSource({ source: data.source, url: data.url });
            return;
          }
          setError(
            `No abstract found. <a href="https://doi.org/${doi}" target="_blank" class="text-blue-600 hover:text-blue-800 underline">View on publisher</a>`,
          );
        } catch {
          setFallbackLoading(false);
          setError('Failed to load paper from any source.');
        }
      } finally {
        setIsLoading(false);
      }
    };

    fetchPaper();
  }, [doi]);

  const handleExtract = async () => {
    if (!doi) return;

    setIsExtracting(true);
    setExtractionError(null);

    try {
      const data = await nerApi.analysePaper(doi, true, searchSource);

      if (!data || 'error' in data) {
        throw new Error(data.error || 'Extraction failed');
      }

      if (data.entities) {
        setEntities(data.entities);
        setPaperData(data);
        setIsExtracted(true);
      }
    } catch (err: unknown) {
      console.error('NER extraction failed:', err);
      setExtractionError(
        await extractErrorDetail(
          err,
          'Extraction timed out or failed. Please try again.',
        ),
      );
    } finally {
      setIsExtracting(false);
    }
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'e' && !e.ctrlKey && !e.metaKey && !e.altKey) {
        const tag = (e.target as HTMLElement).tagName;
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
        if (!isExtracted && !isExtracting) {
          e.preventDefault();
          handleExtract();
        }
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [doi, isExtracted, isExtracting]);

  const pdfIdentifier =
    getPaperIdentifier()?.value || paperData?.pmcid || paperData?.doi || doi;
  // Allow PDF if: full_text mode, OR OpenAlex has direct PDF URL
  const canUsePdfActions =
    (paperData?.mode === 'full_text' || Boolean(paperData?.pdfUrl)) &&
    Boolean(pdfIdentifier);

  const handleDownloadPdf = async () => {
    const startedDoi = doi;
    // OpenAlex has direct PDF URL - open in new tab
    if (paperData?.pdfUrl) {
      window.open(paperData.pdfUrl, '_blank');
      flashDownloadDone();
      return;
    }

    if (!pdfIdentifier || isDownloadingPdf) return;
    setPdfActionError(null);
    setIsDownloadingPdf(true);

    try {
      const { blob, filename } = await paperApi.fetchPdf(pdfIdentifier);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
      if (doiRef.current === startedDoi) flashDownloadDone();
    } catch (err: unknown) {
      console.error('PDF download failed:', err);
      if (doiRef.current === startedDoi) {
        setPdfActionError(
          await extractErrorDetail(
            err,
            'PDF download is not available for this paper.',
          ),
        );
      }
    } finally {
      if (doiRef.current === startedDoi) setIsDownloadingPdf(false);
    }
  };

  if (isLoading || fallbackLoading) {
    return (
      <div className="flex items-center justify-center min-h-[calc(100vh-140px)] w-full py-20">
        <div
          className="flex flex-col items-center justify-center gap-3.5"
          style={{ fontFamily: 'var(--font-google-sans)' }}
        >
          <SpinnerGap size={46} className="animate-spin text-slate-900" />
          <span className="text-[17px] font-medium text-on-surface-variant">
            Loading...
          </span>
        </div>
      </div>
    );
  }

  if (error || !paperData) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center p-8 bg-red-50 rounded-xl">
          <p
            className="text-sm text-red-600 mb-4"
            dangerouslySetInnerHTML={{ __html: error || 'Paper not found' }}
          />
          <button
            onClick={() => navigate({ to: '/' })}
            className="text-primary hover:underline flex items-center gap-2 mx-auto"
          >
            <ArrowLeft size={16} /> Back to search
          </button>
        </div>
      </div>
    );
  }

  // Ensure full HTML contains <h2> section headings
  const hasH2 = paperData.html && /<h2[\s>]/i.test(paperData.html);
  const htmlBlob: string =
    !hasH2 && paperData.sections?.length
      ? paperData.sections
          .map((s, i) => {
            const secId = `section-${i}`;
            const h2 = s.title
              ? `<h2 id="${secId}" class="article-h2">${s.title}</h2>`
              : '';
            return `<section id="${secId}">${h2}${s.content}</section>`;
          })
          .join('')
      : (paperData.html ??
        (paperData.sections?.length
          ? paperData.sections
              .map(
                (s, i) =>
                  `<section id="section-${i}"><h2 class="article-h2">${s.title}</h2>${s.content}</section>`,
              )
              .join('')
          : ''));

  let tocList: TocItem[] =
    paperData.toc && paperData.toc.length > 0
      ? paperData.toc.map((t, i) => ({
          id: t.id || `section-${i}`,
          text: t.text || (t as { title?: string }).title || `Section ${i + 1}`,
          level: t.level || 1,
        }))
      : (paperData.sections?.map((s, i) => ({
          id: `section-${i}`,
          text: s.title,
          level: 1,
        })) ?? []);

  // Universal fallback: if tocList is empty but html contains headings, extract TOC from HTML
  if (tocList.length === 0 && htmlBlob) {
    const h2Matches = Array.from(htmlBlob.matchAll(/<h2([^>]*)>(.*?)<\/h2>/gi));
    if (h2Matches.length > 0) {
      tocList = h2Matches.map((m, i) => {
        const rawText = m[2].replace(/<[^>]+>/g, '').trim();
        const idMatch = m[1].match(/id=["']([^"']+)["']/i);
        return {
          id: idMatch ? idMatch[1] : `section-${i}`,
          text: rawText || `Section ${i + 1}`,
          level: 1,
        };
      });
    }
  }

  // Format title with preserved formatting (italic/bold) for display
  const displayTitle = paperData.title || 'Untitled Paper';

  return (
    <PaperViewer
      paperIdentifier={getPaperIdentifier()}
      mode={paperData.mode}
      title={displayTitle}
      html={htmlBlob}
      toc={tocList}
      entities={entities}
      isExtracted={isExtracted}
      isExtracting={isExtracting}
      extractionError={extractionError}
      fallbackSource={fallbackSource ?? undefined}
      isFetchingFallback={false}
      paperAuthors={paperData.authors || []}
      paperJournal={paperData.journal}
      paperDate={paperData.date}
      isOpenAccess={paperData.isOpenAccess}
      canUsePdfActions={canUsePdfActions}
      isDownloadingPdf={isDownloadingPdf}
      downloadDone={downloadDone}
      pdfActionError={pdfActionError}
      onDownloadPdf={handleDownloadPdf}
      onExtract={handleExtract}
    />
  );
};

export default PaperPage;
