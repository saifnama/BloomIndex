/**
 * Overlay drawer displaying indexed papers, extracted entities, and
 * journals with dynamic filtering, resizing, and offset pagination.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  CaretCircleRight,
  Database,
  FileText,
  Funnel,
  MagnifyingGlass,
  CaretCircleDown,
  X,
  XCircle,
} from '@phosphor-icons/react';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { dbApi } from '../../lib/api/papers';
import { sanitizeHtml } from '../../utils/sanitize';
import { Skeleton } from '@/components/ui/skeleton';

const DEFAULT_W = 440;
const MIN_W = 440;
const getMaxW = () =>
  typeof window !== 'undefined' ? Math.floor(window.innerWidth * 0.35) : 640;

export type DrawerTab = 'papers' | 'entities' | 'journals';

export interface DrawerFilter {
  kind:
    | 'country'
    | 'entity'
    | 'journal'
    | 'year'
    | 'papers'
    | 'entities'
    | 'journals';
  label: string;
  value?: string;
}

interface Props {
  open: boolean;
  onClose: () => void;
  tab: DrawerTab;
  filter: DrawerFilter | null;
  entities: { name: string; value: number }[];
  journals: { name: string; value: number }[];
  onOpenPaper?: (doi: string) => void;
}

interface PaperRow {
  title?: string;
  journal?: string;
  year?: number | string;
  doi?: string;
  entity_count?: number;
}

const TAB_META: Record<DrawerTab, { label: string }> = {
  papers: { label: 'Papers' },
  entities: { label: 'Entities' },
  journals: { label: 'Journals' },
};

const DatabaseExplorerDrawer: React.FC<Props> = ({
  open,
  onClose,
  tab: tabProp,
  filter,
  entities,
  journals,
  onOpenPaper,
}) => {
  // Synchronizes local state when parent changes tabProp during render.
  const [activeTab, setActiveTab] = useState<DrawerTab>(tabProp);
  const [prevTabProp, setPrevTabProp] = useState(tabProp);
  if (prevTabProp !== tabProp) {
    setPrevTabProp(tabProp);
    setActiveTab(tabProp);
  }

  // Preserves internal filter changes while synchronizing parent prop.
  const [activeFilter, setActiveFilter] = useState<DrawerFilter | null>(filter);
  const [prevFilterProp, setPrevFilterProp] = useState(filter);
  if (prevFilterProp !== filter) {
    setPrevFilterProp(filter);
    setActiveFilter(filter);
  }

  const [width, setWidth] = useState<number>(DEFAULT_W);
  const [dragging, setDragging] = useState(false);
  const [papers, setPapers] = useState<PaperRow[] | null>(null);
  const [papersError, setPapersError] = useState(false);
  const [totalPapers, setTotalPapers] = useState<number>(0);
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  const [dropdownOpen, setDropdownOpen] = useState(false);

  // Debounce input keystrokes before initiating paper search queries.
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(searchQuery), 250);
    return () => clearTimeout(t);
  }, [searchQuery]);

  // Renders into dedicated portal root to isolate stacking contexts.
  const [portalRoot] = useState<Element | null>(() =>
    typeof document !== 'undefined'
      ? (document.getElementById('portal-root') ?? document.body)
      : null,
  );

  // ── Drag-to-resize ──────────────────────────────────────────────────────────
  const startDrag = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    setDragging(true);
  }, []);

  useEffect(() => {
    if (!dragging) return;

    const onMove = (e: MouseEvent) => {
      const next = window.innerWidth - e.clientX;
      const clamped = Math.max(MIN_W, Math.min(getMaxW(), next));
      setWidth(clamped);
    };
    const onUp = () => setDragging(false);

    const prevCursor = document.body.style.cursor;
    const prevSelect = document.body.style.userSelect;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);

    return () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      document.body.style.cursor = prevCursor;
      document.body.style.userSelect = prevSelect;
    };
  }, [dragging]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  // Queries indexed papers matching active filter criteria and search terms.
  useEffect(() => {
    if (!open || activeTab !== 'papers') return;

    const country =
      activeFilter?.kind === 'country' ? activeFilter.value : undefined;
    const year = activeFilter?.kind === 'year' ? activeFilter.value : undefined;
    const apiQuery =
      activeFilter?.kind === 'papers'
        ? activeFilter.value
        : debouncedQuery || undefined;

    // eslint-disable-next-line react-hooks/set-state-in-effect -- fetch loading reset
    setPapers(null);
    setPapersError(false);

    dbApi
      .getPapers(50, 0, country, apiQuery, year)
      .then((data) => {
        const list: PaperRow[] = Array.isArray(data)
          ? data
          : (data.papers ?? data.items ?? []);
        setPapers(list);
        setTotalPapers(
          data && !Array.isArray(data) && typeof data.total === 'number'
            ? data.total
            : list.length,
        );
      })
      .catch((err) => {
        console.error('Failed to fetch explorer papers:', err);
        setPapersError(true);
        setPapers([]);
      });
  }, [open, activeTab, activeFilter, debouncedQuery]);

  // Appends next batch of papers using offset pagination.
  const loadMore = () => {
    if (!papers) return;
    const country =
      activeFilter?.kind === 'country' ? activeFilter.value : undefined;
    const year = activeFilter?.kind === 'year' ? activeFilter.value : undefined;
    const apiQuery =
      activeFilter?.kind === 'papers'
        ? activeFilter.value
        : debouncedQuery || undefined;

    dbApi.getPapers(50, papers.length, country, apiQuery, year).then((data) => {
      const list: PaperRow[] = Array.isArray(data)
        ? data
        : (data.papers ?? data.items ?? []);
      setPapers((prev) => [...(prev ?? []), ...list]);
      if (!Array.isArray(data) && typeof data.total === 'number')
        setTotalPapers(data.total);
    });
  };

  const filteredEntities = searchQuery
    ? entities.filter((e) =>
        e.name.toLowerCase().includes(searchQuery.toLowerCase()),
      )
    : entities;

  const filteredJournals = searchQuery
    ? journals.filter((j) =>
        j.name.toLowerCase().includes(searchQuery.toLowerCase()),
      )
    : journals;

  const resultCount =
    activeTab === 'papers'
      ? papers
        ? papers.length
        : 0
      : activeTab === 'entities'
        ? filteredEntities.length
        : filteredJournals.length;

  const hasMore =
    activeTab === 'papers' && papers !== null && papers.length < totalPapers;

  // Alternative tab options available for selection in the header menu.
  const dropdownTabs = (Object.keys(TAB_META) as DrawerTab[]).filter(
    (t) => t !== activeTab,
  );

  const drawer = (
    <aside
      className="flex flex-col overflow-hidden"
      style={{
        fontFamily: 'var(--font-google-sans)',
        position: 'fixed',
        top: 0,
        right: 0,
        bottom: 0,
        width: `${width}px`,
        maxWidth: '100vw',
        zIndex: 9999,
        background: 'var(--surface-lowest, #FFFFFF)',
        borderLeft: '1px solid var(--outline-variant, #E4E4E7)',
        boxShadow: open ? '-2px 0 16px rgba(0,0,0,0.06)' : 'none',
        transform: open ? 'translateX(0)' : 'translateX(100%)',
        transition: dragging
          ? 'none'
          : 'transform .32s cubic-bezier(.2,.7,.2,1), width .12s ease',
        visibility: open ? 'visible' : 'hidden',
        pointerEvents: open ? 'auto' : 'none',
      }}
    >
      {/* Drag resize handle. */}
      <div
        onMouseDown={startDrag}
        title="Drag to resize"
        style={{
          position: 'absolute',
          left: -4,
          top: 0,
          bottom: 0,
          width: 8,
          cursor: 'col-resize',
          zIndex: 10,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <div
          style={{
            width: 3,
            height: 40,
            borderRadius: 99,
            background: dragging
              ? 'var(--teal-400, #26A69A)'
              : 'var(--outline-variant, #E4E4E7)',
            transition: 'background .15s',
          }}
        />
      </div>

      <div
        style={{
          padding: '20px 22px 14px',
          position: 'relative',
          flexShrink: 0,
        }}
      >
        {/* Dismiss drawer button. */}
        <button
          onClick={onClose}
          aria-label="Close"
          style={{
            position: 'absolute',
            top: 16,
            right: 14,
            background: 'none',
            border: 'none',
            cursor: 'pointer',
            color: 'var(--on-surface-variant, #71717A)',
            display: 'grid',
            placeItems: 'center',
            width: 34,
            height: 34,
            borderRadius: '50%',
            transition: 'color .15s, background .15s',
          }}
          onMouseEnter={(e) => {
            (e.currentTarget as HTMLElement).style.color = 'var(--on-surface)';
            (e.currentTarget as HTMLElement).style.background =
              'var(--surface-c)';
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLElement).style.color =
              'var(--on-surface-variant)';
            (e.currentTarget as HTMLElement).style.background = 'transparent';
          }}
        >
          <CaretCircleRight size={26} weight="regular" />
        </button>

        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 13,
            marginBottom: 16,
          }}
        >
          <div
            style={{
              width: 42,
              height: 42,
              borderRadius: '50%',
              background: '#F4F4F5',
              color: '#71717A',
              display: 'grid',
              placeItems: 'center',
              flexShrink: 0,
            }}
          >
            <Database size={20} weight="regular" />
          </div>
          <div
            style={{
              fontSize: 21,
              fontWeight: 700,
              color: 'var(--on-surface, #18181B)',
              letterSpacing: '-0.01em',
              fontFamily: 'var(--font-google-sans)',
            }}
          >
            Database
          </div>
        </div>

        {/* Search query input. */}
        <div style={{ position: 'relative' }}>
          <span
            style={{
              position: 'absolute',
              left: 14,
              top: '50%',
              transform: 'translateY(-50%)',
              color: 'var(--on-surface-variant, #71717A)',
              pointerEvents: 'none',
              display: 'grid',
              placeItems: 'center',
            }}
          >
            <MagnifyingGlass size={16} />
          </span>
          <input
            ref={searchInputRef}
            placeholder="Search title, journal or entities…"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            style={{
              width: '100%',
              height: 40,
              paddingLeft: 42,
              paddingRight: searchQuery ? 38 : 14,
              background: 'var(--surface-c, #F4F4F5)',
              border: '1.5px solid transparent',
              borderRadius: 'var(--radius-full, 9999px)',
              fontSize: 15,
              color: 'var(--on-surface, #18181B)',
              fontFamily: 'var(--font-google-sans)',
              caretColor: '#000000',
              outline: 'none',
              transition:
                'border-color .2s cubic-bezier(0.4, 0, 0.2, 1), box-shadow .2s cubic-bezier(0.4, 0, 0.2, 1)',
            }}
            onFocus={(e) => {
              e.currentTarget.style.borderColor = '#ff6dba';
              e.currentTarget.style.boxShadow =
                '0 0 0 3px color-mix(in oklab, #ff6dba 16%, transparent)';
            }}
            onBlur={(e) => {
              e.currentTarget.style.borderColor = 'transparent';
              e.currentTarget.style.boxShadow = 'none';
            }}
          />
          {searchQuery && (
            <button
              type="button"
              onClick={() => {
                setSearchQuery('');
                searchInputRef.current?.focus();
              }}
              aria-label="Clear search"
              style={{
                position: 'absolute',
                right: 10,
                top: '50%',
                transform: 'translateY(-50%)',
                width: 26,
                height: 26,
                borderRadius: '50%',
                display: 'grid',
                placeItems: 'center',
                background: 'transparent',
                border: 'none',
                color: 'var(--on-surface-variant, #71717A)',
                cursor: 'pointer',
                transition: 'color .15s',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.color = 'var(--on-surface, #18181B)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.color =
                  'var(--on-surface-variant, #71717A)';
              }}
            >
              <XCircle size={16} weight="regular" />
            </button>
          )}
        </div>

        {/* Removable filter tag badge for timeline year or country origin. */}
        {activeFilter &&
          (activeFilter.kind === 'year' || activeFilter.kind === 'country') &&
          activeFilter.value && (
            <div
              style={{ marginTop: 10, display: 'flex', alignItems: 'center' }}
            >
              <button
                type="button"
                onClick={() => setActiveFilter(null)}
                aria-label="Remove"
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 6,
                  padding: '4px 8px 4px 10px',
                  borderRadius: 999,
                  background:
                    activeFilter.kind === 'country'
                      ? 'rgba(6,182,212,0.10)'
                      : 'rgba(0,172,193,0.10)',
                  color:
                    activeFilter.kind === 'country' ? '#0891B2' : '#007A8E',
                  border:
                    activeFilter.kind === 'country'
                      ? '1px solid rgba(6,182,212,0.35)'
                      : '1px solid rgba(0,172,193,0.28)',
                  fontSize: 12,
                  fontWeight: 600,
                  fontFamily: 'var(--font-google-sans)',
                  cursor: 'pointer',
                  transition: 'background .15s ease',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background =
                    activeFilter.kind === 'country'
                      ? 'rgba(6,182,212,0.18)'
                      : 'rgba(0,172,193,0.18)';
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background =
                    activeFilter.kind === 'country'
                      ? 'rgba(6,182,212,0.10)'
                      : 'rgba(0,172,193,0.10)';
                }}
              >
                <span>
                  {activeFilter.kind === 'country'
                    ? `Country: ${activeFilter.value}`
                    : `Year: ${activeFilter.value}`}
                </span>
                <X
                  size={12}
                  weight="bold"
                  style={{ color: 'currentColor', display: 'inline-block' }}
                />
              </button>
            </div>
          )}
      </div>

      {/* Results header and tab selection dropdown. */}
      <div
        style={{
          padding: '7px 18px 7px 22px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          borderTop: '1px solid var(--outline-variant, #E4E4E7)',
          flexShrink: 0,
        }}
      >
        <span
          style={{
            fontSize: 11.5,
            fontWeight: 700,
            color: 'var(--on-surface-variant, #71717A)',
            textTransform: 'uppercase',
            letterSpacing: '.07em',
            fontFamily: 'var(--font-google-sans)',
          }}
        >
          {resultCount} Results
        </span>

        <DropdownMenu open={dropdownOpen} onOpenChange={setDropdownOpen}>
          <DropdownMenuTrigger asChild>
            <button
              aria-label="Switch"
              style={{
                background: dropdownOpen ? 'var(--surface-c, #F4F4F5)' : 'none',
                border: 'none',
                cursor: 'pointer',
                display: 'grid',
                placeItems: 'center',
                width: 30,
                height: 30,
                borderRadius: 8,
                color: dropdownOpen
                  ? '#000000'
                  : 'var(--on-surface-variant, #71717A)',
                transition: 'background .15s, color .15s',
              }}
              onMouseEnter={(e) => {
                if (!dropdownOpen) {
                  (e.currentTarget as HTMLElement).style.background =
                    'var(--surface-c)';
                  (e.currentTarget as HTMLElement).style.color =
                    'var(--on-surface)';
                }
              }}
              onMouseLeave={(e) => {
                if (!dropdownOpen) {
                  (e.currentTarget as HTMLElement).style.background = 'none';
                  (e.currentTarget as HTMLElement).style.color =
                    'var(--on-surface-variant)';
                }
              }}
            >
              <Funnel
                size={15}
                weight={dropdownOpen ? 'fill' : 'regular'}
                style={{
                  color: dropdownOpen ? '#000000' : 'inherit',
                  transition: 'color .15s',
                }}
              />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            align="end"
            style={{
              borderRadius: 14,
              minWidth: 140,
              width: 140,
              zIndex: 10000,
              backgroundColor: '#FFFFFF',
              border: '1px solid var(--outline-variant, #E4E4E7)',
              boxShadow: '0 4px 12px rgba(0, 0, 0, 0.08)',
              padding: '6px',
            }}
          >
            {dropdownTabs.map((t) => (
              <DropdownMenuItem
                key={t}
                onClick={() => {
                  setActiveTab(t);
                  setDropdownOpen(false);
                }}
                style={{
                  fontSize: 13.5,
                  fontWeight: 500,
                  cursor: 'pointer',
                  padding: '8px 12px',
                  borderRadius: 10,
                  fontFamily: 'var(--font-google-sans)',
                }}
              >
                {TAB_META[t].label}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <div className="flex-1 overflow-y-auto nice-scroll flex flex-col gap-2 px-[18px] pt-[10px] pb-2">
        {activeTab === 'papers' && (
          <PapersList
            papers={papers}
            error={papersError}
            onOpen={(doi) => onOpenPaper?.(doi)}
          />
        )}
        {activeTab === 'entities' && (
          <EntitiesList entities={filteredEntities} filter={filter} />
        )}
        {activeTab === 'journals' && (
          <JournalsList journals={filteredJournals} filter={filter} />
        )}

        {/* Paginate more results button. */}
        {hasMore && (
          <div
            style={{
              display: 'flex',
              justifyContent: 'center',
              padding: '12px 0 8px',
            }}
          >
            <button
              onClick={loadMore}
              aria-label="Load more"
              title="Load more"
              style={{
                background: 'none',
                border: 'none',
                cursor: 'pointer',
                color: '#18181B',
                padding: 4,
                borderRadius: '50%',
                display: 'grid',
                placeItems: 'center',
                transition: 'opacity .15s, transform .18s',
              }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLElement).style.opacity = '0.55';
                (e.currentTarget as HTMLElement).style.transform =
                  'translateY(3px)';
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLElement).style.opacity = '1';
                (e.currentTarget as HTMLElement).style.transform =
                  'translateY(0)';
              }}
            >
              <CaretCircleDown size={28} weight="regular" />
            </button>
          </div>
        )}
      </div>
    </aside>
  );

  if (!portalRoot) return null;
  return createPortal(drawer, portalRoot);
};

const PASTEL_PALETTES = [
  { bg: '#E0F2FE', color: '#0284C7' }, // Sky
  { bg: '#FCE7F3', color: '#DB2777' }, // Pink
  { bg: '#EDE9FE', color: '#7C3AED' }, // Purple
  { bg: '#FEF3C7', color: '#D97706' }, // Amber
  { bg: '#DCFCE7', color: '#16A34A' }, // Mint
  { bg: '#FFEDD5', color: '#EA580C' }, // Orange
  { bg: '#CCFBF1', color: '#0D9488' }, // Teal
  { bg: '#F1F5F9', color: '#475569' }, // Slate
  { bg: '#FAE8FF', color: '#C026D3' }, // Fuchsia
  { bg: '#E0E7FF', color: '#4F46E5' }, // Indigo
  { bg: '#FFE4E6', color: '#E11D48' }, // Rose
  { bg: '#ECFCCB', color: '#65A30D' }, // Lime
];

function getPastelColor(seed: string | number) {
  const str = String(seed);
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    hash = (hash << 5) - hash + str.charCodeAt(i);
    hash |= 0;
  }
  const index = Math.abs(hash) % PASTEL_PALETTES.length;
  return PASTEL_PALETTES[index];
}

interface PapersListProps {
  papers: PaperRow[] | null;
  error: boolean;
  onOpen: (doi: string) => void;
}

function PapersList({ papers, error, onOpen }: PapersListProps) {
  if (error) {
    return (
      <div
        style={{
          padding: '28px 0',
          textAlign: 'center',
          fontSize: 14,
          color: 'var(--on-surface-muted)',
          fontFamily: 'var(--font-google-sans)',
        }}
      >
        Failed to load papers
      </div>
    );
  }
  if (papers === null) {
    return (
      <div className="space-y-3 py-2" aria-label="Loading">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="flex gap-3 items-center">
            <Skeleton className="h-10 w-10 shrink-0 rounded-full" />
            <div className="flex-1 space-y-2 pt-1">
              <Skeleton className="h-3.5 w-full rounded-full" />
              <Skeleton className="h-3 w-3/4 rounded-full" />
            </div>
          </div>
        ))}
      </div>
    );
  }
  if (papers.length === 0) {
    return (
      <div
        style={{
          padding: '28px 0',
          textAlign: 'center',
          fontSize: 14,
          color: 'var(--on-surface-muted)',
          fontFamily: 'var(--font-google-sans)',
        }}
      >
        No papers found
      </div>
    );
  }

  return (
    <>
      {papers.map((p, i) => {
        const pastel = getPastelColor(p.doi || p.title || i);
        return (
          <div
            key={i}
            onClick={() => p.doi && onOpen(p.doi)}
            style={{
              padding: '13px 15px',
              borderRadius: 'var(--radius-md, 12px)',
              background: '#FFFFFF',
              border: '1px solid var(--outline-variant, #E4E4E7)',
              display: 'flex',
              alignItems: 'center',
              gap: 14,
              cursor: p.doi ? 'pointer' : 'default',
              transition: 'border-color .18s ease, box-shadow .18s ease',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.borderColor = '#ff6dba';
              e.currentTarget.style.boxShadow =
                '0 2px 12px rgba(255, 109, 186, 0.18)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.borderColor =
                'var(--outline-variant, #E4E4E7)';
              e.currentTarget.style.boxShadow = 'none';
            }}
          >
            {/* Adaptive pastel badge icon. */}
            <div
              style={{
                width: 42,
                height: 42,
                borderRadius: '50%',
                background: pastel.bg,
                color: pastel.color,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                flexShrink: 0,
                alignSelf: 'center',
              }}
            >
              <FileText size={20} weight="regular" />
            </div>

            <div style={{ flex: 1, minWidth: 0 }}>
              <div
                style={{
                  fontSize: 14.5,
                  lineHeight: 1.45,
                  fontWeight: 600,
                  color: 'var(--on-surface, #18181B)',
                  marginBottom: 6,
                  textAlign: 'left',
                  fontFamily: 'var(--font-google-sans)',
                }}
                dangerouslySetInnerHTML={{
                  __html: sanitizeHtml(p.title ?? '(untitled)'),
                }}
              />
              {/* Journal name and publication year metadata. */}
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                  fontSize: 13,
                  color: 'var(--on-surface-variant, #71717A)',
                  flexWrap: 'wrap',
                  fontFamily: 'var(--font-google-sans)',
                }}
              >
                {p.journal && (
                  <span
                    style={{
                      fontStyle: 'italic',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                      maxWidth: 200,
                    }}
                  >
                    {p.journal}
                  </span>
                )}
                {p.journal && p.year && (
                  <span
                    style={{ color: 'var(--outline, #A1A1AA)', fontSize: 11 }}
                  >
                    •
                  </span>
                )}
                {p.year && <span>{p.year}</span>}
              </div>
            </div>
          </div>
        );
      })}
    </>
  );
}

function EntitiesList({
  entities,
  filter,
}: {
  entities: { name: string; value: number }[];
  filter: DrawerFilter | null;
}) {
  const filterValue =
    filter?.kind === 'entity' ? filter.value?.toLowerCase() : null;
  if (entities.length === 0) {
    return (
      <div
        style={{
          padding: '28px 0',
          textAlign: 'center',
          fontSize: 14,
          color: 'var(--on-surface-muted)',
          fontFamily: 'var(--font-google-sans)',
        }}
      >
        No entities match this filter
      </div>
    );
  }
  return (
    <>
      {entities.map((e, i) => {
        const isMatch =
          filterValue && e.name.toLowerCase().includes(filterValue);
        return (
          <div
            key={i}
            style={{
              padding: '10px 16px',
              borderBottom: '1px solid var(--outline-variant, #E4E4E7)',
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              background: isMatch
                ? 'color-mix(in oklab, var(--primary) 8%, transparent)'
                : 'transparent',
              transition: 'background .12s',
            }}
          >
            <span
              style={{
                fontSize: 14,
                fontWeight: 600,
                color: 'var(--on-surface, #18181B)',
                flex: 1,
                textAlign: 'left',
                textTransform: 'capitalize',
                fontFamily: 'var(--font-google-sans)',
              }}
            >
              {e.name.replace(/_/g, ' ')}
            </span>
            <span
              style={{
                fontSize: 12,
                fontFamily: 'var(--font-google-sans)',
                fontVariantNumeric: 'tabular-nums',
                color: '#7C3AED',
                fontWeight: 700,
                background: '#F3E8FF',
                padding: '2px 8px',
                borderRadius: 99,
              }}
            >
              {e.value.toLocaleString()}
            </span>
          </div>
        );
      })}
    </>
  );
}

function JournalsList({
  journals,
  filter,
}: {
  journals: { name: string; value: number }[];
  filter: DrawerFilter | null;
}) {
  const filterValue = filter?.kind === 'journal' ? filter.value : null;
  if (journals.length === 0) {
    return (
      <div
        style={{
          padding: '28px 0',
          textAlign: 'center',
          fontSize: 14,
          color: 'var(--on-surface-muted)',
          fontFamily: 'var(--font-google-sans)',
        }}
      >
        No journals match this filter
      </div>
    );
  }
  const max = journals[0]?.value ?? 1;
  return (
    <>
      {journals.map((j, i) => {
        const pct = Math.max(2, Math.round((j.value / max) * 100));
        const isMatch = filterValue && j.name === filterValue;
        return (
          <div
            key={i}
            style={{
              padding: '10px 16px',
              borderBottom: '1px solid var(--outline-variant, #E4E4E7)',
              background: isMatch
                ? 'color-mix(in oklab, var(--primary) 8%, transparent)'
                : 'transparent',
              transition: 'background .12s',
            }}
          >
            <div
              style={{
                fontSize: 14,
                fontWeight: 600,
                color: 'var(--on-surface, #18181B)',
                marginBottom: 7,
                textAlign: 'left',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                fontFamily: 'var(--font-google-sans)',
              }}
            >
              {j.name}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <div
                style={{
                  flex: 1,
                  height: 5,
                  background: 'var(--surface-c, #F4F4F5)',
                  borderRadius: 99,
                  overflow: 'hidden',
                }}
              >
                <div
                  style={{
                    height: '100%',
                    width: `${pct}%`,
                    background: 'var(--teal-500, #009688)',
                    borderRadius: 99,
                  }}
                />
              </div>
              <span
                style={{
                  fontSize: 12,
                  fontFamily: 'var(--font-google-sans)',
                  fontVariantNumeric: 'tabular-nums',
                  color: 'var(--teal-700, #00796B)',
                  fontWeight: 700,
                  minWidth: 32,
                  textAlign: 'right',
                }}
              >
                {j.value}
              </span>
            </div>
          </div>
        );
      })}
    </>
  );
}

export default DatabaseExplorerDrawer;
