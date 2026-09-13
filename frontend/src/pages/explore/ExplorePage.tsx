import React, {
  useState,
  useEffect,
  useRef,
  useMemo,
  lazy,
  Suspense,
} from 'react';
import { useNavigate, getRouteApi } from '@tanstack/react-router';
import { useShallow } from 'zustand/react/shallow';
import SearchBar from '@/features/search/SearchBar';
import { SearchFilterContext } from '@/features/search/SearchFilterContext';
import { Skeleton } from '@/components/ui/skeleton';
import { nerApi } from '../../lib/api/papers';
import { useSearchStore } from '../../stores/searchStore';
import { formatTextWithFormatting } from '../../utils/sanitize';
import type { SearchFilters, SearchResult } from '../../types';
import {
  CaretDown,
  ArrowUp,
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  Circle,
  LockSimpleOpen,
  Article,
  SpinnerGap,
} from '@phosphor-icons/react';

const route = getRouteApi('/');

const Dashboard = lazy(() =>
  import('@/features/search/Dashboard').then((m) => ({ default: m.default })),
);

type SortType = 'Relevance' | 'Citations' | 'Date';
type SortDir = 'asc' | 'desc';
type SourceKey = 'europepmc' | 'openalex';

/** Compare two SearchFilters objects for value equality. Cheap fixed-
 * shape compare — avoids pulling lodash for one call. */
function filtersEqual(a: SearchFilters | null, b: SearchFilters): boolean {
  if (!a) return false;
  return (
    a.open_access === b.open_access &&
    a.has_full_text === b.has_full_text &&
    (a.article_type ?? '') === (b.article_type ?? '') &&
    (a.sort ?? '') === (b.sort ?? '') &&
    (a.source ?? 'europepmc') === (b.source ?? 'europepmc')
  );
}

const sortFromRaw = (
  raw: string | undefined | null,
): { type: SortType; dir: SortDir } =>
  raw === 'cited'
    ? { type: 'Citations', dir: 'desc' }
    : raw === 'date'
      ? { type: 'Date', dir: 'desc' }
      : raw === 'date_asc'
        ? { type: 'Date', dir: 'asc' }
        : { type: 'Relevance', dir: 'desc' };

const sortToRaw = (v: { type: SortType; dir: SortDir }): string =>
  v.type === 'Citations'
    ? 'cited'
    : v.type === 'Date'
      ? v.dir === 'asc'
        ? 'date_asc'
        : 'date'
      : '';

// ─── small UI atoms — co-located so ExplorePage stays a single file ─────────────

interface FilterSectionProps {
  title: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}

const FilterSection: React.FC<FilterSectionProps> = ({
  title,
  defaultOpen = false,
  children,
}) => {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        style={{ fontFamily: 'var(--font-google-sans)' }}
        className={`
          flex w-full items-center justify-between bg-transparent border-0 cursor-pointer
          py-1.5 text-[12px] font-bold uppercase tracking-[0.14em] text-on-surface
          hover:text-on-surface-variant transition-colors
          ${open ? 'mb-3.5' : 'mb-0'}
          transition-[margin-bottom] duration-200
        `}
      >
        <span>{title}</span>
        <CaretDown
          size={12}
          weight="bold"
          className="text-on-surface-variant transition-transform duration-200"
          style={{ transform: open ? 'rotate(180deg)' : 'rotate(0deg)' }}
        />
      </button>
      {open && (
        <div className="flex flex-col gap-2.5 animate-fade-up">{children}</div>
      )}
    </div>
  );
};

interface SourcePillProps {
  role: SourceKey;
  label: string;
  active: boolean;
  onClick: () => void;
}

const SourcePill: React.FC<SourcePillProps> = ({
  role,
  label,
  active,
  onClick,
}) => {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`pill pill-${role}`}
      data-active={active ? 'true' : 'false'}
    >
      <span>{label}</span>
    </button>
  );
};

interface FilterTogglePillProps {
  icon: React.ReactNode;
  label: string;
  tone: 'orange' | 'blue';
  active: boolean;
  onClick: () => void;
}

const FilterTogglePill: React.FC<FilterTogglePillProps> = ({
  icon,
  label,
  tone,
  active,
  onClick,
}) => {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className="pill"
      data-active={active ? 'true' : 'false'}
      data-tone={tone}
    >
      {icon}
      <span>{label}</span>
    </button>
  );
};

interface SortSegmentedProps {
  value: { type: SortType; dir: SortDir };
  onChange: (v: { type: SortType; dir: SortDir }) => void;
}

/** Relevance | Citations | Date — plain text with thin vertical dividers.
 * Active = bold + full-contrast color. Inactive = dim. Click Date when
 * active = flip ↑/↓ direction. */
const SortSegmented: React.FC<SortSegmentedProps> = ({ value, onChange }) => {
  const options: SortType[] = ['Relevance', 'Citations', 'Date'];
  return (
    <div
      style={{ fontFamily: 'var(--font-google-sans)' }}
      className="inline-flex items-center"
    >
      {options.map((opt, i) => {
        const active = opt === value.type;
        const isDate = opt === 'Date';
        const Arrow = value.dir === 'asc' ? ArrowUp : ArrowDown;
        return (
          <React.Fragment key={opt}>
            {i > 0 && (
              <span aria-hidden className="w-px h-3.5 mx-3.5 bg-border" />
            )}
            <button
              type="button"
              onClick={() => {
                if (active && isDate) {
                  onChange({
                    ...value,
                    dir: value.dir === 'asc' ? 'desc' : 'asc',
                  });
                } else if (!active) {
                  onChange({
                    type: opt,
                    dir: isDate ? value.dir || 'desc' : 'desc',
                  });
                }
              }}
              style={{ fontFamily: 'var(--font-google-sans)' }}
              className={`
                inline-flex items-center gap-1.5 bg-transparent border-0 p-0 cursor-pointer
                text-[15px] transition-colors duration-150
                ${
                  active
                    ? 'font-semibold text-on-surface'
                    : 'font-normal text-on-surface-variant hover:text-on-surface'
                }
              `}
            >
              <span>{opt}</span>
              {isDate && active && (
                <Arrow
                  size={14}
                  weight="bold"
                  className="shrink-0 -translate-y-[1px]"
                />
              )}
            </button>
          </React.Fragment>
        );
      })}
    </div>
  );
};

interface CircleCheckProps {
  checked: boolean;
  onToggle: () => void;
  label: string;
  count?: number | null;
}

const CircleCheck: React.FC<CircleCheckProps> = ({
  checked,
  onToggle,
  label,
  count,
}) => (
  <label
    onClick={onToggle}
    style={{ fontFamily: 'var(--font-google-sans)' }}
    className="flex items-center gap-2.5 cursor-pointer text-[15px] text-on-surface"
  >
    <Circle
      size={18}
      weight={checked ? 'fill' : 'regular'}
      className={checked ? 'text-[#ff6dba]' : 'text-on-surface-muted'}
      style={checked ? { color: '#ff6dba' } : undefined}
    />
    <span className="flex-1 leading-snug">{label}</span>
    {count != null && (
      <span className="mono text-xs text-on-surface-muted tabular-nums">
        {count}
      </span>
    )}
  </label>
);

// ─── result card per design spec ────────────────────────────────────────────

interface ResultCardProps {
  result: SearchResult;
  onOpen: () => void;
  /** Animation delay so consecutive cards stagger in (40 + idx*60 ms). */
  delayMs: number;
}

const ResultCard: React.FC<ResultCardProps> = ({ result, onOpen, delayMs }) => {
  const year = result.year || '';
  const authors = result.authors || '';
  const journal = result.journal || '';
  const isOA = !!result.isOpenAccess;
  const hasFT = !!result.hasFullText;
  const citationCount =
    typeof result.citationCount === 'number' ? result.citationCount : null;

  return (
    <article
      onClick={onOpen}
      style={{
        animationDelay: `${delayMs}ms`,
        fontFamily: 'var(--font-google-sans)',
      }}
      className="
        pq-result-card card is-hoverable relative px-6 py-5
        cursor-pointer
      "
    >
      {/* Top row — DOI left, year right */}
      <div className="flex items-center justify-between mb-2.5">
        {result.doi ? (
          <a
            href={`https://doi.org/${result.doi}`}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="mono text-[13px] font-medium text-blue-600 hover:text-blue-800 no-underline hover:underline transition-colors truncate max-w-[65%]"
          >
            {result.doi}
          </a>
        ) : (
          <span />
        )}
        <span
          className="text-[13px] font-medium text-on-surface-variant"
          style={{ fontFamily: 'var(--font-google-sans)' }}
        >
          {year}
        </span>
      </div>

      {/* Title — serif, bold */}
      <h3
        className="
          mb-5
          font-bold text-[19px] leading-snug text-on-surface
        "
        style={{ fontFamily: 'var(--font-serif)' }}
        dangerouslySetInnerHTML={{
          __html: formatTextWithFormatting(result.title || ''),
        }}
      />

      {/* Meta — authors • journal (italic) • citations (left) + Open Access & Full Text (right) */}
      <div
        className="flex items-center justify-between gap-2 text-[13.5px] text-on-surface-variant mb-3.5"
        style={{ fontFamily: 'var(--font-google-sans)' }}
      >
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 min-w-0">
          {authors && (
            <span
              dangerouslySetInnerHTML={{
                __html: formatTextWithFormatting(authors),
              }}
            />
          )}
          {journal && (
            <>
              <span className="text-on-surface-muted">•</span>
              <span className="italic">{journal}</span>
            </>
          )}
          {citationCount != null && citationCount > 0 && (
            <>
              <span className="text-on-surface-muted">•</span>
              <span>{citationCount} citations</span>
            </>
          )}
        </div>

        {(isOA || hasFT) && (
          <span
            className="ml-auto flex items-center gap-1 shrink-0"
            onClick={(e) => e.stopPropagation()}
          >
            {isOA && (
              <span
                className="status-ic"
                title="Open Access"
                style={{ color: '#E65100' }}
              >
                <LockSimpleOpen size={17} weight="regular" />
              </span>
            )}
            {hasFT && (
              <span className="status-ic" title="Full Text">
                <Article size={18} weight="regular" color="#1565C0" />
              </span>
            )}
          </span>
        )}
      </div>

      {/* Excerpt (abstract) */}
      {result.abstract && (
        <div
          className="text-sm text-on-surface-variant leading-relaxed line-clamp-3"
          style={{ fontFamily: 'var(--font-google-sans)' }}
          dangerouslySetInnerHTML={{
            __html: formatTextWithFormatting(result.abstract),
          }}
        />
      )}
    </article>
  );
};

const ResultCardSkeleton: React.FC<{ delayMs?: number }> = ({
  delayMs = 0,
}) => (
  <article
    style={{ animationDelay: `${delayMs}ms` }}
    className="
      pq-result-card card relative px-6 py-5 rounded-2xl border border-border bg-card
      flex flex-col gap-3.5 animate-in fade-in duration-300
    "
  >
    {/* Top row — DOI left, year right */}
    <div className="flex items-center justify-between">
      <Skeleton className="h-3.5 w-40 rounded" />
      <Skeleton className="h-3.5 w-12 rounded" />
    </div>

    {/* Title — serif title lines */}
    <div className="space-y-2">
      <Skeleton className="h-5 w-4/5 rounded" />
      <Skeleton className="h-5 w-3/5 rounded" />
    </div>

    {/* Meta line — authors • journal • badges */}
    <div className="flex flex-wrap items-center gap-2.5">
      <Skeleton className="h-3.5 w-32 rounded" />
      <span className="text-on-surface-muted text-xs">•</span>
      <Skeleton className="h-3.5 w-24 rounded" />
      <Skeleton className="h-5 w-20 rounded-full" />
      <Skeleton className="h-3.5 w-16 rounded" />
    </div>

    {/* Abstract snippet */}
    <div className="space-y-2 pt-0.5">
      <Skeleton className="h-3.5 w-full rounded" />
      <Skeleton className="h-3.5 w-11/12 rounded" />
      <Skeleton className="h-3.5 w-3/4 rounded" />
    </div>

    {/* Entity tags row */}
    <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-border/50">
      <Skeleton className="h-6 w-20 rounded-full" />
      <Skeleton className="h-6 w-24 rounded-full" />
      <Skeleton className="h-6 w-16 rounded-full" />
      <Skeleton className="h-6 w-28 rounded-full" />
    </div>
  </article>
);

// ─── filter sidebar ─────────────────────────────────────────────────────────

const EUROPEPMC_TYPES = ['Research Articles', 'Review Articles'] as const;
// Single fallback list — full OpenAlex types (loaded statically; the live
// list is fetched into the SearchBar dropdown but for the sidebar's
// checkbox list a static set is fine and keeps the markup deterministic).
const OPENALEX_TYPES = [
  'Article',
  'Preprint',
  'Review',
  'Dissertation',
  'Letter',
  'Book',
  'Book Chapter',
  'Erratum',
  'Editorial',
  'Paratext',
  'Reference Entry',
  'Report',
  'Dataset',
  'Peer Review',
  'Other',
  'Retraction',
  'Supplementary Materials',
  'Report Component',
  'Database',
  'Standard',
  'Grant',
] as const;

interface FilterSidebarProps {
  filters: SearchFilters;
  onChange: (next: SearchFilters) => void;
}

const FilterSidebar: React.FC<FilterSidebarProps> = ({ filters, onChange }) => {
  const source = (filters.source ?? 'europepmc') as SourceKey;
  return (
    <aside
      className="
        mt-[92px] h-fit self-start
        flex flex-col gap-7 pr-6 pb-2
        border-r border-border
      "
    >
      <div
        style={{ fontFamily: 'var(--font-google-sans)' }}
        className="text-[19px] font-bold tracking-[-0.01em] text-on-surface shrink-0 mb-1"
      >
        Filter by
      </div>

      <FilterSection title="Sources" defaultOpen>
        <div className="flex flex-col gap-2 items-start">
          <SourcePill
            role="europepmc"
            label="Europe PMC"
            active={source === 'europepmc'}
            onClick={() =>
              onChange({ ...filters, source: 'europepmc', article_type: '' })
            }
          />
          <SourcePill
            role="openalex"
            label="OpenAlex"
            active={source === 'openalex'}
            onClick={() =>
              onChange({ ...filters, source: 'openalex', article_type: '' })
            }
          />
        </div>
      </FilterSection>

      {source === 'europepmc' && (
        <FilterSection title="Availability" defaultOpen>
          <div className="flex flex-col gap-2 items-start">
            <FilterTogglePill
              icon={<LockSimpleOpen size={14} weight="regular" />}
              label="Open Access"
              tone="orange"
              active={!!filters.open_access}
              onClick={() =>
                onChange({ ...filters, open_access: !filters.open_access })
              }
            />
            <FilterTogglePill
              icon={<Article size={14} weight="regular" color="#1565C0" />}
              label="Full Text"
              tone="blue"
              active={!!filters.has_full_text}
              onClick={() =>
                onChange({ ...filters, has_full_text: !filters.has_full_text })
              }
            />
          </div>
        </FilterSection>
      )}

      <FilterSection title="Type" defaultOpen>
        <div
          style={{ scrollbarWidth: 'none', msOverflowStyle: 'none' }}
          className="flex flex-col gap-2.5 max-h-[380px] overflow-y-auto scrollbar-hide pr-1"
        >
          {(source === 'openalex' ? OPENALEX_TYPES : EUROPEPMC_TYPES).map(
            (t) => {
              // Map UI label back to backend key.
              const key =
                source === 'openalex'
                  ? t.toLowerCase().replace(/\s+/g, '-')
                  : t === 'Research Articles'
                    ? 'Research-article'
                    : t === 'Review Articles'
                      ? 'Review'
                      : '';
              const checked = filters.article_type === key;
              return (
                <CircleCheck
                  key={t}
                  label={t}
                  checked={checked}
                  onToggle={() =>
                    onChange({ ...filters, article_type: checked ? '' : key })
                  }
                />
              );
            },
          )}
        </div>
      </FilterSection>
    </aside>
  );
};

const FilterSidebarSkeleton: React.FC = () => (
  <aside
    className="
      mt-[92px] h-fit self-start
      flex flex-col gap-7 pr-6 pb-2
      border-r border-border
      animate-in fade-in duration-300
    "
  >
    <Skeleton className="h-5 w-20 rounded mb-1" />

    {/* Sources */}
    <div className="space-y-3">
      <Skeleton className="h-3.5 w-16 rounded" />
      <div className="flex flex-col gap-2">
        <Skeleton className="h-8 w-28 rounded-full" />
        <Skeleton className="h-8 w-24 rounded-full" />
      </div>
    </div>

    {/* Availability */}
    <div className="space-y-3">
      <Skeleton className="h-3.5 w-20 rounded" />
      <div className="flex flex-col gap-2">
        <Skeleton className="h-8 w-32 rounded-full" />
        <Skeleton className="h-8 w-28 rounded-full" />
      </div>
    </div>

    {/* Type */}
    <div className="space-y-3">
      <Skeleton className="h-3.5 w-14 rounded" />
      <div className="space-y-2.5">
        <div className="flex items-center gap-2.5">
          <Skeleton className="h-4 w-4 rounded-full" />
          <Skeleton className="h-3.5 w-32 rounded" />
        </div>
        <div className="flex items-center gap-2.5">
          <Skeleton className="h-4 w-4 rounded-full" />
          <Skeleton className="h-3.5 w-28 rounded" />
        </div>
        <div className="flex items-center gap-2.5">
          <Skeleton className="h-4 w-4 rounded-full" />
          <Skeleton className="h-3.5 w-36 rounded" />
        </div>
      </div>
    </div>
  </aside>
);

// ─── main page ──────────────────────────────────────────────────────────────

const ExplorePage: React.FC = () => {
  // ── Persisted search state (sessionStorage-backed, per-tab) ─────────────
  // Universal fix: useShallow for array slices so getSnapshot is cached
  const results = useSearchStore(useShallow((s) => s.results));
  const pagination = useSearchStore((s) => s.pagination);
  const currentPage = useSearchStore((s) => s.currentPage);
  const lastQuery = useSearchStore((s) => s.lastQuery);
  const lastFilters = useSearchStore((s) => s.lastFilters);
  const scrollY = useSearchStore((s) => s.scrollY);
  const setSearchResult = useSearchStore((s) => s.setSearchResult);
  const setScrollY = useSearchStore((s) => s.setScrollY);

  // Transient (intentionally NOT persisted)
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchingQuery, setSearchingQuery] = useState('');
  const [searchingFilters, setSearchingFilters] =
    useState<SearchFilters | null>(null);
  const navigate = useNavigate();
  // Universal fix: pick primitives via select so getSnapshot is cached (whole search object is new each parse)
  const q = route.useSearch({ select: (s) => s.q });
  const oa = route.useSearch({ select: (s) => s.oa });
  const ft = route.useSearch({ select: (s) => s.ft });
  const type = route.useSearch({ select: (s) => s.type });
  const sort = route.useSearch({ select: (s) => s.sort });
  const src = route.useSearch({ select: (s) => s.src });
  const didInitFromUrl = useRef(false);

  // Memoized so SearchBar doesn't get a new object every render (caused getSnapshot loop + typing lag)
  const defaultFilters = useMemo<SearchFilters>(
    () => ({
      open_access: oa === '1',
      has_full_text: ft === '1',
      article_type: type ?? '',
      sort: sort ?? '',
      source: (src ?? 'europepmc') as SearchFilters['source'],
    }),
    [oa, ft, type, sort, src],
  );
  const defaultQuery = q ?? '';

  const doSearch = async (
    query: string,
    filters: SearchFilters,
    page: number = 1,
  ) => {
    setSearchingQuery(query);
    setSearchingFilters(filters);
    setIsLoading(true);
    setError(null);
    try {
      const data = await nerApi.search(
        query,
        filters,
        page,
        filters.source || 'europepmc',
      );
      if ('error' in data && data.error) {
        throw new Error(data.error);
      }
      setSearchResult({
        results: data.results || [],
        pagination: data.pagination || null,
        currentPage: page,
        lastQuery: query,
        lastFilters: filters,
      });
      navigate({
        to: '/',
        search: {
          q: query,
          oa: filters.open_access ? '1' : undefined,
          ft: filters.has_full_text ? '1' : undefined,
          type: filters.article_type || undefined,
          sort: filters.sort || undefined,
          src:
            filters.source && filters.source !== 'europepmc'
              ? filters.source
              : undefined,
        },
        replace: true,
      });
    } catch (err: unknown) {
      console.error('Search failed:', err);
      const e = err as {
        response?: { data?: { error?: string; detail?: string } };
        message?: string;
      };
      setError(
        e?.response?.data?.error ||
          e?.response?.data?.detail ||
          e?.message ||
          'Search failed. Please try again.',
      );
      setSearchResult({
        results: [],
        pagination: null,
        currentPage: 1,
        lastQuery: query,
        lastFilters: filters,
      });
    } finally {
      setIsLoading(false);
    }
  };

  // Restore on mount: if URL query matches cache, paint cached results.
  // Mount-once initialization (didInitFromUrl guard) — intentionally not
  // derived state; the doSearch call is a fetch side effect (see disable below).
  useEffect(() => {
    if (didInitFromUrl.current) return;
    didInitFromUrl.current = true;

    if (!q) return;
    const filters: SearchFilters = {
      open_access: oa === '1',
      has_full_text: ft === '1',
      article_type: type ?? '',
      sort: sort ?? '',
      source: src ?? 'europepmc',
    };
    const cacheMatches =
      results.length > 0 &&
      lastQuery === q &&
      filtersEqual(lastFilters, filters);
    if (cacheMatches) {
      requestAnimationFrame(() => window.scrollTo({ top: scrollY }));
      return;
    }
    // eslint-disable-next-line react-hooks/set-state-in-effect -- mount init + fetch
    doSearch(q, filters, 1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Track scroll position so navigating back lands where we left off.
  useEffect(() => {
    let frame: number | null = null;
    const onScroll = () => {
      if (frame !== null) return;
      frame = requestAnimationFrame(() => {
        setScrollY(window.scrollY);
        frame = null;
      });
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      window.removeEventListener('scroll', onScroll);
      if (frame !== null) cancelAnimationFrame(frame);
    };
  }, [setScrollY]);

  const handleSearch = async (query: string, filters: SearchFilters) => {
    // Detect identifier patterns and short-circuit to the paper viewer.
    const isIdentifier = (q: string) => {
      const trimmed = q.trim();
      return (
        /^10\.\d{4,}/.test(trimmed) ||
        /^https?:\/\/(dx\.)?doi\.org\//.test(trimmed) ||
        /^doi:/i.test(trimmed) ||
        /^PMC\d+/i.test(trimmed) ||
        /^https?:\/\/(www\.)?pubmed\.ncbi\.nlm\.nih\.gov\//.test(trimmed) ||
        /^https?:\/\/www\.ncbi\.nlm\.nih\.gov\/pmc\//.test(trimmed) ||
        /^https?:\/\/pmc\.ncbi\.nlm\.nih\.gov\/articles\//.test(trimmed) ||
        /^https?:\/\/europepmc\.org\/article\//.test(trimmed) ||
        /^\d+$/.test(trimmed)
      );
    };
    if (isIdentifier(query.trim())) {
      let cleanID = query.trim();
      if (cleanID.startsWith('http') && /(dx\.)?doi\.org\//.test(cleanID)) {
        cleanID = cleanID.replace(/^https?:\/\/(dx\.)?doi\.org\//, '');
      } else if (cleanID.toLowerCase().startsWith('doi:')) {
        cleanID = cleanID.substring(4).trim();
      } else if (/(www\.)?pubmed\.ncbi\.nlm\.nih\.gov/.test(cleanID)) {
        const m = cleanID.match(/\/(\d+)/);
        if (m) cleanID = m[1];
      } else if (
        /ncbi\.nlm\.nih\.gov\/pmc/.test(cleanID) ||
        /pmc\.ncbi\.nlm\.nih\.gov/.test(cleanID)
      ) {
        const m = cleanID.match(/PMC\d+/i);
        if (m) cleanID = m[0].toUpperCase();
      } else if (/europepmc\.org\/article\/PMC\//.test(cleanID)) {
        const m = cleanID.match(/PMC\d+/i);
        if (m) cleanID = m[0].toUpperCase();
      } else if (/europepmc\.org\/article\/MED\//.test(cleanID)) {
        const m = cleanID.match(/MED\/(\d+)/i);
        if (m) cleanID = m[1];
      }
      navigate({ to: '/paper/$doi', params: { doi: cleanID } });
      return;
    }
    doSearch(query, filters, 1);
  };

  const handleNextPage = () => {
    if (lastQuery && lastFilters && pagination?.hasMore) {
      window.scrollTo({ top: 0, behavior: 'smooth' });
      doSearch(lastQuery, lastFilters, currentPage + 1);
    }
  };

  const handlePrevPage = () => {
    if (lastQuery && lastFilters && currentPage > 1) {
      window.scrollTo({ top: 0, behavior: 'smooth' });
      doSearch(lastQuery, lastFilters, currentPage - 1);
    }
  };

  // Filter sidebar wiring — when user toggles a filter, re-run search
  // with the same query + updated filter set.
  const handleFilterChange = (next: SearchFilters) => {
    if (!lastQuery) return;
    doSearch(lastQuery, next, 1);
  };

  // Sort segmented control — also re-runs the search.
  const sortValue = useMemo(
    () => sortFromRaw(lastFilters?.sort),
    [lastFilters?.sort],
  );
  const handleSortChange = (v: { type: SortType; dir: SortDir }) => {
    if (!lastQuery || !lastFilters) return;
    const nextFilters: SearchFilters = { ...lastFilters, sort: sortToRaw(v) };
    doSearch(lastQuery, nextFilters, 1);
  };

  // ── render ────────────────────────────────────────────────────────────
  const hasResults = results.length > 0;
  const currentQuery = lastQuery || searchingQuery;
  const noResults =
    !isLoading && !error && currentQuery && results.length === 0;
  const totalCount = pagination?.total ?? (hasResults ? results.length : 0);
  const isSearching = isLoading;
  const showResultsLayout = hasResults || isSearching || !!lastQuery;

  return (
    <div className="w-full px-8 pt-7 pb-10 results-page">
      <div className="mx-auto max-w-[1440px]">
        {/* Homepage — search bar with expandable filters + Dashboard */}
        {!showResultsLayout && !error && (
          <>
            <div className="px-[24px]">
              <SearchBar
                onSearch={handleSearch}
                isLoading={isLoading}
                defaultQuery={defaultQuery}
                defaultFilters={defaultFilters}
              />
            </div>
            <Suspense
              fallback={
                <div
                  className="flex flex-col items-center justify-center py-28 gap-3.5"
                  style={{ fontFamily: 'var(--font-google-sans)' }}
                >
                  <SpinnerGap
                    size={46}
                    className="animate-spin text-slate-900"
                  />
                  <span className="text-[17px] font-medium text-on-surface-variant">
                    Loading...
                  </span>
                </div>
              }
            >
              <Dashboard />
            </Suspense>
          </>
        )}

        {/* Global error on homepage before any search view */}
        {error && !showResultsLayout && (
          <>
            <div className="px-[24px] mb-6">
              <SearchBar
                onSearch={handleSearch}
                isLoading={isLoading}
                defaultQuery={defaultQuery}
                defaultFilters={defaultFilters}
              />
            </div>
            <div className="mx-auto mb-6 max-w-4xl rounded-lg bg-red-50 p-4 text-red-700">
              {error}
            </div>
          </>
        )}

        {/* Results layout: Used for both active results and Skeleton loading */}
        {showResultsLayout && (
          <SearchFilterContext.Provider value={true}>
            <div
              className="grid gap-10 mt-1"
              style={{ gridTemplateColumns: '240px 1fr' }}
            >
              {/* Sidebar: Real sidebar when filters exist, Skeleton when starting fresh from homepage */}
              {isSearching && !lastFilters && !searchingFilters ? (
                <FilterSidebarSkeleton />
              ) : (
                <FilterSidebar
                  filters={lastFilters || searchingFilters || defaultFilters}
                  onChange={handleFilterChange}
                />
              )}

              <section>
                {/* SearchBar: Compact and aligned in the right section (same width as results) */}
                <div className="mb-8">
                  <SearchBar
                    onSearch={handleSearch}
                    isLoading={isLoading}
                    defaultQuery={searchingQuery || lastQuery || defaultQuery}
                    defaultFilters={
                      lastFilters || searchingFilters || defaultFilters
                    }
                  />
                </div>

                {error && (
                  <div className="mb-6 rounded-lg bg-red-50 p-4 text-red-700">
                    {error}
                  </div>
                )}

                {noResults ? (
                  /* No-results message */
                  <div
                    className="flex justify-center px-5 pt-[90px] pb-10"
                    style={{ fontFamily: 'var(--font-google-sans)' }}
                  >
                    <p className="text-[16px] italic text-on-surface-variant">
                      No publications were found for that term.
                    </p>
                  </div>
                ) : (
                  <>
                    {/* Header row — "1,247 publications found" + SortSegmented */}
                    {isSearching ? (
                      <div className="flex items-center justify-between mb-5">
                        <Skeleton className="h-5 w-44 rounded-md" />
                        <Skeleton className="h-8 w-56 rounded-full" />
                      </div>
                    ) : (
                      <div className="flex items-center justify-between mb-5">
                        <div
                          style={{ fontFamily: 'var(--font-google-sans)' }}
                          className="text-[15px] text-on-surface whitespace-nowrap"
                        >
                          <strong className="font-semibold text-on-surface">
                            {totalCount.toLocaleString()}
                          </strong>{' '}
                          <span className="text-on-surface-variant">
                            publications found
                          </span>
                        </div>
                        <SortSegmented
                          value={sortValue}
                          onChange={handleSortChange}
                        />
                      </div>
                    )}

                    {/* Result cards or Skeletons */}
                    {isSearching ? (
                      <div className="flex flex-col gap-4">
                        {Array.from({ length: 4 }).map((_, i) => (
                          <ResultCardSkeleton key={i} delayMs={i * 60} />
                        ))}
                      </div>
                    ) : (
                      <div className="flex flex-col gap-4">
                        {results.map((r, i) => (
                          <ResultCard
                            key={r.id || i}
                            result={r}
                            delayMs={40 + Math.min(i, 7) * 60}
                            onOpen={() => {
                              const paperId = r.doi || r.pmcid || r.pmid;
                              if (paperId) {
                                const src = (
                                  r.source || 'Europe PMC'
                                ).toLowerCase();
                                navigate({
                                  to: '/paper/$doi',
                                  params: { doi: paperId },
                                  search: { src },
                                });
                              }
                            }}
                          />
                        ))}
                      </div>
                    )}

                    {/* Pagination */}
                    {pagination && !isSearching && (
                      <div className="mt-8 mb-4 flex items-center justify-center gap-3">
                        {currentPage > 1 && (
                          <button
                            type="button"
                            onClick={handlePrevPage}
                            disabled={isLoading}
                            aria-label="Previous"
                            title="Previous"
                            className="
                              grid h-10 w-10 place-items-center rounded-full
                              bg-transparent text-on-surface border-0 cursor-pointer
                              hover:bg-surface-c transition-colors duration-150
                              disabled:opacity-40 disabled:cursor-default disabled:hover:bg-transparent
                            "
                          >
                            <ArrowLeft size={18} weight="bold" />
                          </button>
                        )}
                        {pagination.hasMore && (
                          <button
                            type="button"
                            onClick={handleNextPage}
                            disabled={isLoading}
                            aria-label="Next"
                            title="Next"
                            className="
                              grid h-10 w-10 place-items-center rounded-full
                              bg-transparent text-on-surface border-0 cursor-pointer
                              hover:bg-surface-c transition-colors duration-150
                              disabled:opacity-40 disabled:cursor-default disabled:hover:bg-transparent
                            "
                          >
                            <ArrowRight size={18} weight="bold" />
                          </button>
                        )}
                      </div>
                    )}
                  </>
                )}
              </section>
            </div>
          </SearchFilterContext.Provider>
        )}
      </div>
    </div>
  );
};

export default ExplorePage;
