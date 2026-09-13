import React, {
  useState,
  useEffect,
  useCallback,
  useRef,
  useContext,
} from 'react';
import {
  MagnifyingGlass,
  XCircle,
  CaretDown,
  LockSimpleOpen,
  Article,
  ArrowUp,
  ArrowDown,
  Command,
} from '@phosphor-icons/react';
import type { SearchFilters } from '../../types';
import { searchTypesApi } from '../../lib/api/dashboard';

import { SearchFilterContext } from './SearchFilterContext';

/**
 * SearchBar — pill-shaped search bar with expandable filters.
 *
 * Design:
 *   - Idle: just the input + magnifying glass + ⌘K Kbd hint (right side).
 *   - Focused / expanded: two rows below the divider:
 *       Row 1 — SOURCES (Europe PMC → OpenAlex → Database) + AVAILABILITY
 *               (Open Access + Full Text — Europe PMC only)
 *       Row 2 — SORT BY (Type dropdown + Relevance/Citations/Date with
 *               inline ↑/↓ for Date)
 *   - The X clear icon (no background) appears when there's text.
 *   - On Enter: a pink trail-light streaks along the bottom inner edge
 *     before submission completes.
 *
 * Source semantics:
 *   - europepmc / openalex → submit to ``/search/json``; current BloomIndex
 *     search flow (no backend change).
 *   - database → call ``onOpenDatabasePanel(query)`` instead of submitting;
 *     parent opens the existing DatabaseExplorerDrawer with the query pre-filled.
 *
 * Conditional filters:
 *   - database → no Availability, no Sort, no Type — just the source pill.
 *   - europepmc → Availability + Sort + Type (Research Articles / Review).
 *   - openalex → Sort + Type (full 21-item OpenAlex vocabulary, fetched).
 */

type SourceKey = 'europepmc' | 'openalex';
type SortType = 'Relevance' | 'Citations' | 'Date';
type SortDir = 'asc' | 'desc';

interface SearchBarProps {
  onSearch: (query: string, filters: SearchFilters) => void;
  isLoading?: boolean;
  defaultQuery?: string;
  defaultFilters?: SearchFilters;
}

interface ArticleTypeOption {
  key: string;
  display_name: string;
  count: number | null;
}

const EUROPEPMC_TYPES: ArticleTypeOption[] = [
  { key: '', display_name: 'Type', count: null },
  { key: 'Research-article', display_name: 'Research Articles', count: null },
  { key: 'Review', display_name: 'Review Articles', count: null },
];

const OPENALEX_FALLBACK: ArticleTypeOption[] = [
  { key: '', display_name: 'Type', count: null },
  { key: 'article', display_name: 'Article', count: null },
  { key: 'preprint', display_name: 'Preprint', count: null },
  { key: 'review', display_name: 'Review', count: null },
  { key: 'dissertation', display_name: 'Dissertation', count: null },
  { key: 'letter', display_name: 'Letter', count: null },
  { key: 'book', display_name: 'Book', count: null },
  { key: 'book-chapter', display_name: 'Book Chapter', count: null },
  { key: 'erratum', display_name: 'Erratum', count: null },
  { key: 'editorial', display_name: 'Editorial', count: null },
  { key: 'paratext', display_name: 'Paratext', count: null },
  { key: 'reference-entry', display_name: 'Reference Entry', count: null },
  { key: 'report', display_name: 'Report', count: null },
  { key: 'dataset', display_name: 'Dataset', count: null },
  { key: 'peer-review', display_name: 'Peer Review', count: null },
  { key: 'other', display_name: 'Other', count: null },
  { key: 'retraction', display_name: 'Retraction', count: null },
  {
    key: 'supplementary-materials',
    display_name: 'Supplementary Materials',
    count: null,
  },
  { key: 'report-component', display_name: 'Report Component', count: null },
  { key: 'database', display_name: 'Database', count: null },
  { key: 'standard', display_name: 'Standard', count: null },
  { key: 'grant', display_name: 'Grant', count: null },
];

const titleCase = (s: string): string =>
  s
    .split(/[-_\s]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(' ');

let cachedOpenAlexTypes: ArticleTypeOption[] = OPENALEX_FALLBACK;
let openAlexTypesPromise: Promise<void> | null = null;

const ensureOpenAlexTypes = () => {
  if (!openAlexTypesPromise) {
    openAlexTypesPromise = searchTypesApi
      .getTypes('openalex')
      .then((data) => {
        if (data.types && data.types.length > 0) {
          const mapped: ArticleTypeOption[] = data.types.map((t) => ({
            key: t.key,
            display_name: titleCase(t.display_name || t.key),
            count: t.count ?? null,
          }));
          cachedOpenAlexTypes = [
            { key: '', display_name: 'Type', count: null },
            ...mapped,
          ];
        }
      })
      .catch((err) => {
        console.error('Failed to fetch OpenAlex types:', err);
      });
  }
  return openAlexTypesPromise;
};

// ─── small UI atoms ─────────────────────────────────────────────────────────

interface SourcePillProps {
  role: SourceKey;
  label: string;
  count?: number;
  active: boolean;
  onClick: () => void;
}

const SourcePill: React.FC<SourcePillProps> = ({
  role,
  label,
  count,
  active,
  onClick,
}) => {
  const tone = {
    europepmc: 'pill-europepmc',
    openalex: 'pill-openalex',
  }[role];
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`
        pill ${tone}
        ${active ? 'data-[active=true]' : ''}
      `}
      data-active={active ? 'true' : 'false'}
    >
      <span>{label}</span>
      {count != null && (
        <span className="grid place-items-center min-w-[28px] h-5 px-1.5 rounded-full bg-background/70 text-[11px] font-bold">
          {count}
        </span>
      )}
    </button>
  );
};

interface FilterPillProps {
  icon: React.ReactNode;
  label: string;
  tone: 'orange' | 'blue';
  active: boolean;
  onClick: () => void;
}

const FilterPill: React.FC<FilterPillProps> = ({
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

interface DropdownProps {
  label: string;
  value: string;
  options: ArticleTypeOption[];
  onChange: (key: string) => void;
}

const Dropdown: React.FC<DropdownProps> = ({
  label,
  value,
  options,
  onChange,
}) => {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!open) return;
    const onMouseDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node))
        setOpen(false);
    };
    document.addEventListener('mousedown', onMouseDown);
    return () => document.removeEventListener('mousedown', onMouseDown);
  }, [open]);
  const display = options.find((o) => o.key === value)?.display_name ?? label;
  return (
    <div ref={ref} className="relative inline-block">
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          setOpen((o) => !o);
        }}
        style={{ fontFamily: 'var(--font-google-sans)' }}
        className={`
          inline-flex items-center justify-between gap-2 h-[34px] px-3 rounded-full
          ${open ? 'bg-surface-c' : 'bg-transparent'}
          border border-border text-on-surface text-[13.5px] font-medium
          transition-colors cursor-pointer
        `}
      >
        <span>{display}</span>
        <CaretDown
          size={11}
          weight="bold"
          className="text-on-surface-variant"
        />
      </button>
      {open && (
        <div
          className="
            absolute top-[calc(100%+6px)] left-0 z-[100]
            min-w-[170px] max-h-[300px] overflow-y-auto
            bg-background border border-border/80 rounded-[12px]
            shadow-[0_4px_20px_-2px_rgba(0,0,0,0.12),0_2px_6px_-1px_rgba(0,0,0,0.06)]
            ring-1 ring-black/[0.05] dark:ring-white/[0.08]
            p-1 flex flex-col gap-0.5
            animate-in fade-in slide-in-from-top-1 duration-150
          "
        >
          {options.map((opt) => {
            const active = opt.key === value;
            return (
              <button
                key={opt.key || '__type__'}
                type="button"
                onClick={() => {
                  onChange(opt.key);
                  setOpen(false);
                }}
                style={{ fontFamily: 'var(--font-google-sans)' }}
                className={`
                  flex w-full items-center justify-between px-2.5 py-1.5 rounded-[8px] text-[13px] leading-5
                  ${active ? 'bg-surface-c font-medium text-on-surface' : 'text-on-surface hover:bg-surface-c/70 font-normal'}
                  transition-colors cursor-pointer text-left
                `}
              >
                <span className="truncate">{opt.display_name}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
};

interface SortControlProps {
  value: { type: SortType; dir: SortDir };
  onChange: (v: { type: SortType; dir: SortDir }) => void;
}

const SortControl: React.FC<SortControlProps> = ({ value, onChange }) => {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!open) return;
    const onMouseDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node))
        setOpen(false);
    };
    document.addEventListener('mousedown', onMouseDown);
    return () => document.removeEventListener('mousedown', onMouseDown);
  }, [open]);
  const isDate = value.type === 'Date';
  const Arrow = value.dir === 'asc' ? ArrowUp : ArrowDown;
  const sortOptions: SortType[] = ['Relevance', 'Citations', 'Date'];
  return (
    <div ref={ref} className="relative inline-flex">
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          setOpen((o) => !o);
        }}
        style={{ fontFamily: 'var(--font-google-sans)' }}
        className={`
          inline-flex items-center gap-1.5 h-[34px] px-3 rounded-full
          ${open ? 'bg-surface-c' : 'bg-transparent'}
          border border-border text-on-surface text-[13.5px] font-medium
          transition-colors cursor-pointer
        `}
      >
        <span>{value.type}</span>
        {isDate && <Arrow size={12} weight="bold" />}
        <CaretDown
          size={11}
          weight="bold"
          className="ml-0.5 text-on-surface-muted"
        />
      </button>
      {open && (
        <div
          className="
            absolute top-[calc(100%+6px)] left-0 z-[100]
            min-w-[150px] max-h-[280px] overflow-y-auto
            bg-background border border-border/80 rounded-[12px]
            shadow-[0_4px_20px_-2px_rgba(0,0,0,0.12),0_2px_6px_-1px_rgba(0,0,0,0.06)]
            ring-1 ring-black/[0.05] dark:ring-white/[0.08]
            p-1 flex flex-col gap-0.5
            animate-in fade-in slide-in-from-top-1 duration-150
          "
        >
          {sortOptions.map((opt) => {
            const active = opt === value.type;
            return (
              <button
                key={opt}
                type="button"
                onClick={() => {
                  if (opt === 'Date' && active) {
                    setOpen(false);
                    return;
                  }
                  onChange({
                    type: opt,
                    dir: opt === 'Date' ? value.dir || 'desc' : 'desc',
                  });
                  if (opt !== 'Date') setOpen(false);
                }}
                style={{ fontFamily: 'var(--font-google-sans)' }}
                className={`
                  flex w-full items-center justify-between px-2.5 py-1.5 rounded-[8px] text-[13px] leading-5
                  ${active ? 'bg-surface-c font-medium text-on-surface' : 'text-on-surface hover:bg-surface-c/70 font-normal'}
                  transition-colors cursor-pointer text-left
                `}
              >
                <span className="truncate">{opt}</span>
                {opt === 'Date' && (
                  <div className="flex items-center gap-1.5 shrink-0 ml-2">
                    <span
                      role="button"
                      title={
                        value.dir === 'asc'
                          ? 'Ascending (oldest first)'
                          : 'Descending (newest first)'
                      }
                      aria-label={
                        value.dir === 'asc'
                          ? 'Ascending — click for descending'
                          : 'Descending — click for ascending'
                      }
                      onClick={(e) => {
                        e.stopPropagation();
                        onChange({
                          ...value,
                          dir: value.dir === 'asc' ? 'desc' : 'asc',
                        });
                      }}
                      className="grid place-items-center p-0.5 rounded hover:bg-surface-high/80 text-on-surface-variant hover:text-on-surface cursor-pointer transition-colors"
                    >
                      <Arrow size={12} weight="bold" />
                    </span>
                  </div>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
};

// ─── main component ─────────────────────────────────────────────────────────

const SearchBar: React.FC<SearchBarProps> = ({
  onSearch,
  isLoading = false,
  defaultQuery = '',
  defaultFilters,
}) => {
  // Automatically hides filter rows when a FilterSidebar is present in the tree
  const hideFilters = useContext(SearchFilterContext);
  const [query, setQuery] = useState(defaultQuery);
  const [filters, setFilters] = useState<SearchFilters>({
    open_access: defaultFilters?.open_access ?? false,
    has_full_text: defaultFilters?.has_full_text ?? false,
    article_type: defaultFilters?.article_type ?? '',
    sort: defaultFilters?.sort ?? '',
    source: defaultFilters?.source ?? 'europepmc',
  });

  // SortControl uses a richer { type, dir } shape — derive it from
  // filters.sort and write back through a single setter so the underlying
  // string-based contract with ExplorePage stays the same.
  const sortValue: { type: SortType; dir: SortDir } =
    filters.sort === 'cited'
      ? { type: 'Citations', dir: 'desc' }
      : filters.sort === 'date'
        ? { type: 'Date', dir: 'desc' }
        : filters.sort === 'date_asc'
          ? { type: 'Date', dir: 'asc' }
          : { type: 'Relevance', dir: 'desc' };
  const setSortValue = (v: { type: SortType; dir: SortDir }) => {
    const raw =
      v.type === 'Citations'
        ? 'cited'
        : v.type === 'Date'
          ? v.dir === 'asc'
            ? 'date_asc'
            : 'date'
          : '';
    setFilters((prev) => ({ ...prev, sort: raw }));
  };

  const [typeOptions, setTypeOptions] = useState<ArticleTypeOption[]>(() =>
    defaultFilters?.source === 'openalex'
      ? cachedOpenAlexTypes
      : EUROPEPMC_TYPES,
  );
  const [expanded, setExpanded] = useState(false);
  const [searching, setSearching] = useState(false);
  const [searchKey, setSearchKey] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // ⌘K / Ctrl+K → focus & expand
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setExpanded(true);
        inputRef.current?.focus();
        inputRef.current?.select();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // Click-outside / Esc → collapse
  useEffect(() => {
    if (!expanded) return;
    const onMouseDown = (e: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setExpanded(false);
        inputRef.current?.blur();
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setExpanded(false);
        inputRef.current?.blur();
      }
    };
    document.addEventListener('mousedown', onMouseDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onMouseDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [expanded]);

  // Sync from URL when navigating back. Render-time adjustment: the query
  // is derived from defaultQuery, applied in the same commit.
  const [prevDefaultQuery, setPrevDefaultQuery] = useState(defaultQuery);
  if (prevDefaultQuery !== defaultQuery) {
    setPrevDefaultQuery(defaultQuery);
    setQuery(defaultQuery);
  }

  // Sync from defaultFilters when the parent gives us new filter props.
  // This used to be a render-phase setState, which React 19 turns into an
  // infinite re-render loop ("Too many re-renders" / minified error #301)
  // because the functional-updater bail-out is not honoured during render.
  // An effect keyed on the (memoized) defaultFilters identity is the safe
  // equivalent: when the values already match, returning `prev` skips the
  // re-render entirely.
  useEffect(() => {
    if (!defaultFilters) return;
    setFilters((prev) => {
      const same =
        prev.open_access === (defaultFilters.open_access ?? false) &&
        prev.has_full_text === (defaultFilters.has_full_text ?? false) &&
        prev.article_type === (defaultFilters.article_type ?? '') &&
        prev.sort === (defaultFilters.sort ?? '') &&
        prev.source === (defaultFilters.source ?? 'europepmc');
      if (same) return prev;
      return {
        open_access: defaultFilters.open_access ?? false,
        has_full_text: defaultFilters.has_full_text ?? false,
        article_type: defaultFilters.article_type ?? '',
        sort: defaultFilters.sort ?? '',
        source: defaultFilters.source ?? 'europepmc',
      };
    });
  }, [defaultFilters]);

  // Fetch types when source changes (Europe PMC = static; OpenAlex = instant cached + background sync)
  const fetchTypeOptions = useCallback((source: string) => {
    if (source === 'europepmc') {
      setTypeOptions(EUROPEPMC_TYPES);
      return;
    }
    if (source === 'openalex') {
      // Immediately display cached / fallback OpenAlex types with 0ms delay
      setTypeOptions(cachedOpenAlexTypes);
      // Silently sync latest types in the background without blocking UI
      ensureOpenAlexTypes().then(() => {
        setTypeOptions(cachedOpenAlexTypes);
      });
    }
  }, []);

  // Fetch-on-source-change has a side effect (network + background sync):
  // it cannot move to render. The setTypeOptions calls are fetch lifecycle
  // (per-call disable below).
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- fetch side effect
    fetchTypeOptions(filters.source);
  }, [filters.source, fetchTypeOptions]);

  // Switching source resets article_type to "" (different option set)
  const handleSourceChange = (newSource: SourceKey) => {
    setFilters((prev) => ({ ...prev, source: newSource, article_type: '' }));
  };

  const clear = () => {
    setQuery('');
    inputRef.current?.focus();
  };

  const submit = (e?: React.FormEvent) => {
    e?.preventDefault();
    const q = query.trim();
    if (!q) return;
    setSearchKey((k) => k + 1);
    setSearching(true);
    setExpanded(false);
    inputRef.current?.blur();
    onSearch(q, filters);
    window.setTimeout(() => setSearching(false), 400);
  };

  const showAvailability =
    !hideFilters && expanded && filters.source === 'europepmc';
  const showSort = !hideFilters && expanded;

  return (
    <div ref={containerRef} className="relative z-20 mb-8 w-full">
      {/* Ambient halo glow — signature pink, only active on expand */}
      {expanded && (
        <div
          aria-hidden
          className="pointer-events-none absolute -inset-x-8 -top-10 -z-10 h-56 opacity-60 blur-3xl"
          style={{
            background: `
              radial-gradient(50% 60% at 25% 40%, color-mix(in oklab, #ff6dba 26%, transparent) 0%, transparent 70%),
              radial-gradient(50% 60% at 75% 30%, color-mix(in oklab, #ff85c8 20%, transparent) 0%, transparent 70%)
            `,
          }}
        />
      )}
      <div
        className={`
          search-shell
          ${expanded ? 'is-focused' : ''}
          transition-[padding] duration-200
        `}
        style={{ padding: '10px 12px 10px 16px' }}
      >
        {/* Pink trail-light on submit — keyed by submission index so animation restarts */}
        {searching && <span className="pq-search-trail" key={searchKey} />}

        <form onSubmit={submit}>
          {/* Row 1 — icon (left corner) | input | clear / ⌘K (right corner) */}
          <div
            role="search"
            onClick={() => {
              setExpanded(true);
              inputRef.current?.focus();
            }}
            className="flex items-center gap-2.5 transition-[padding] duration-200"
          >
            {/* Search icon — clickable, at left corner of pill */}
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                e.preventDefault();
                submit();
              }}
              aria-label="Search"
              className="
                grid place-items-center shrink-0 w-[40px] h-[40px]
                text-on-surface-variant hover:text-[#ff6dba]
                transition-colors rounded-full
              "
            >
              <MagnifyingGlass size={22} weight="regular" />
            </button>
            <input
              ref={inputRef}
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onFocus={() => setExpanded(true)}
              placeholder="Search scholarly publications (DOI, PMID, PMCID, or keywords)…"
              autoComplete="off"
              spellCheck={false}
              style={{
                fontFamily: 'var(--font-google-sans)',
                fontSize: '17.5px',
              }}
              className="
                flex-1 min-w-0 h-[44px]
                bg-transparent border-none outline-none
                text-[17.5px] text-on-surface placeholder:text-on-surface-muted caret-black
                tracking-[-0.003em]
              "
            />
            {query && (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  clear();
                }}
                aria-label="Clear search"
                className="grid place-items-center w-[40px] h-[40px] rounded-full text-on-surface-variant hover:text-on-surface bg-transparent transition-colors shrink-0"
              >
                <XCircle size={22} weight="regular" />
              </button>
            )}
            {!expanded && !query && (
              <span className="inline-flex items-center gap-1.5 shrink-0 mr-2.5">
                <span className="inline-flex items-center justify-center w-[28px] h-[28px] rounded-full bg-[var(--surface-c)] border border-[var(--outline-variant)] text-[var(--on-surface-variant)]">
                  <Command size={14} weight="regular" />
                </span>
                <span className="inline-flex items-center justify-center w-[28px] h-[28px] rounded-full bg-[var(--surface-c)] border border-[var(--outline-variant)] text-[12px] font-mono text-[var(--on-surface-variant)]">
                  K
                </span>
              </span>
            )}
          </div>

          {/* Filter rows — animated expand/collapse via grid-rows. Hidden when hideFilters=true. */}
          {!hideFilters && (
            <div
              className={`grid transition-[grid-template-rows,opacity] duration-300 ease-out ${expanded ? 'overflow-visible' : 'overflow-hidden'}`}
              style={{
                gridTemplateRows: expanded ? '1fr' : '0fr',
                opacity: expanded ? 1 : 0,
              }}
            >
              <div
                className={`min-h-0 ${expanded ? 'overflow-visible' : 'overflow-hidden'}`}
              >
                {/* Row 2 — SOURCES | AVAILABILITY */}
                <div
                  className="
                  flex items-center gap-6 flex-wrap
                  border-t border-border mt-2.5
                  px-4 pt-3.5 pb-1.5
                  animate-spring-in
                "
                >
                  <div className="flex items-center gap-3">
                    <span
                      style={{ fontFamily: 'var(--font-google-sans)' }}
                      className="text-[12px] font-bold uppercase tracking-[0.14em] text-on-surface min-w-[78px]"
                    >
                      Sources
                    </span>
                    <div className="flex items-center gap-2 flex-wrap">
                      <SourcePill
                        role="europepmc"
                        label="Europe PMC"
                        active={filters.source === 'europepmc'}
                        onClick={() => handleSourceChange('europepmc')}
                      />
                      <SourcePill
                        role="openalex"
                        label="OpenAlex"
                        active={filters.source === 'openalex'}
                        onClick={() => handleSourceChange('openalex')}
                      />
                    </div>
                  </div>

                  {showAvailability && (
                    <>
                      <span aria-hidden className="w-px h-6 bg-surface-c" />
                      <div className="flex items-center gap-3">
                        <span
                          style={{ fontFamily: 'var(--font-google-sans)' }}
                          className="text-[12px] font-bold uppercase tracking-[0.14em] text-on-surface"
                        >
                          Availability
                        </span>
                        <div className="flex items-center gap-2 flex-wrap">
                          <FilterPill
                            icon={<LockSimpleOpen size={14} weight="regular" />}
                            label="Open Access"
                            tone="orange"
                            active={filters.open_access}
                            onClick={() =>
                              setFilters((p) => ({
                                ...p,
                                open_access: !p.open_access,
                              }))
                            }
                          />
                          <FilterPill
                            icon={
                              <Article
                                size={14}
                                weight="regular"
                                color="#1565C0"
                              />
                            }
                            label="Full Text"
                            tone="blue"
                            active={filters.has_full_text}
                            onClick={() =>
                              setFilters((p) => ({
                                ...p,
                                has_full_text: !p.has_full_text,
                              }))
                            }
                          />
                        </div>
                      </div>
                    </>
                  )}
                </div>

                {/* Row 3 — SORT BY */}
                {showSort && (
                  <div
                    className="flex items-center gap-3 px-4 pt-1.5 pb-1.5 animate-spring-in"
                    style={{ animationDelay: '0.05s' }}
                  >
                    <span
                      style={{ fontFamily: 'var(--font-google-sans)' }}
                      className="text-[12px] font-bold uppercase tracking-[0.14em] text-on-surface min-w-[78px]"
                    >
                      Sort by
                    </span>
                    <div className="flex items-center gap-2 flex-wrap">
                      <Dropdown
                        label="Type"
                        value={filters.article_type}
                        options={typeOptions}
                        onChange={(k) =>
                          setFilters((p) => ({ ...p, article_type: k }))
                        }
                      />
                      <SortControl value={sortValue} onChange={setSortValue} />
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
          {/* end !hideFilters */}

          {/* Loading shimmer when an outer fetch is in flight — signature pink */}
          {isLoading && (
            <span
              aria-hidden
              className="
                absolute left-4 right-4 bottom-0 h-[2px] rounded-full
                bg-gradient-to-r from-transparent via-[#ff6dba] to-transparent
                shadow-[0_0_8px_rgba(255,109,186,0.6)]
                animate-pulse pointer-events-none
              "
            />
          )}
        </form>
      </div>
    </div>
  );
};

export default SearchBar;
