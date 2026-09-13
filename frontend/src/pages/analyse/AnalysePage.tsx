import { useState, useMemo, useRef, useEffect } from 'react';
import {
  Plus,
  X,
  TrashSimple,
  FileArrowUp,
  ListBullets,
  Graph,
  SidebarSimple,
  DotsThreeVertical,
  ChartBar,
  CaretDown,
  CaretUp,
  SpinnerGap,
} from '@phosphor-icons/react';
import {
  KnowledgeGraph,
  type KnowledgeGraphHandle,
} from '@/features/reader/KnowledgeGraph';
import { downloadGraphHtml } from '../../utils/exportGraphHtml';
import { CompareMatrix } from './CompareMatrix';
import type { Entity } from '../../types';
import { useShallow } from 'zustand/react/shallow';
import { useAnalyseStore, type UploadedPaper } from '../../stores/analyseStore';

const PdfIcon = ({
  size = 24,
  className = '',
}: {
  size?: number;
  className?: string;
}) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 160 160"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    className={className}
  >
    <rect width="160" height="160" rx="28" fill="#E21101" />
    <text
      x="50%"
      y="54%"
      textAnchor="middle"
      fill="#FFFFFF"
      fontFamily="Arial, Helvetica, sans-serif"
      fontSize="56"
      fontWeight="700"
      dominantBaseline="middle"
    >
      PDF
    </text>
  </svg>
);

const ENTITY_GROUP_ORDER = [
  'CHEMICAL',
  'SPECIES',
  'PLANT PART',
  'DEVELOPMENT STAGE',
  'EXTRACTION METHOD',
  'ANALYTICAL TECHNIQUE',
  'BIOACTIVITY',
  'DISEASE',
  'SEASON',
  'LOCATION',
] as const;

const LABEL_NAMES: Record<string, string> = {
  CHEMICAL: 'Chemical',
  SPECIES: 'Species',
  'PLANT PART': 'Plant Part',
  'ANALYTICAL TECHNIQUE': 'Analytical Technique',
  'EXTRACTION METHOD': 'Extraction Method',
  BIOACTIVITY: 'Bioactivity',
  'DEVELOPMENT STAGE': 'Development Stage',
  SEASON: 'Season',
  DISEASE: 'Disease',
  LOCATION: 'Location',
};

const getEntityAccentVar = (label: string) =>
  `--entity-${label.toLowerCase().replace(/[\s_]+/g, '-')}`;
const getEntityAccentColor = (label: string) =>
  `var(${getEntityAccentVar(label)})`;

const AnalysePage = () => {
  // ── Persisted analyse state (sessionStorage-backed, per-tab) ────────────
  // useShallow for array/object slices avoids getSnapshot loop
  const papers = useAnalyseStore(useShallow((s) => s.papers));
  const selectedPaperId = useAnalyseStore((s) => s.selectedPaperId);
  const expandedGroups = useAnalyseStore(useShallow((s) => s.expandedGroups));
  const isCompareMode = useAnalyseStore((s) => s.isCompareMode);
  const compareSelection = useAnalyseStore(
    useShallow((s) => s.compareSelection),
  );
  const addPapers = useAnalyseStore((s) => s.addPapers);
  const removePaper = useAnalyseStore((s) => s.removePaper);
  const setSelectedPaperId = useAnalyseStore((s) => s.setSelectedPaperId);
  const setExpandedGroups = useAnalyseStore((s) => s.setExpandedGroups);
  const toggleGroup = useAnalyseStore((s) => s.toggleGroup);
  const setIsCompareMode = useAnalyseStore((s) => s.setIsCompareMode);
  const setCompareSelection = useAnalyseStore((s) => s.setCompareSelection);
  const toggleCompareSelection = useAnalyseStore(
    (s) => s.toggleCompareSelection,
  );
  const clearCompareSelection = useAnalyseStore((s) => s.clearCompareSelection);

  // ── Derived: selectedPaper is looked up from id at render time ──────────
  const selectedPaper = useMemo(
    () => papers.find((p) => p.id === selectedPaperId) ?? null,
    [papers, selectedPaperId],
  );

  // ── Transient state (intentionally NOT persisted) ───────────────────────
  // viewerSrc is a blob: URL revoked on close → persisting would leave
  // a dangling reference next tab. Upload progress + click-outside menu
  // are pure UI affordances.
  const [viewerPaper, setViewerPaper] = useState<UploadedPaper | null>(null);
  const [viewerSrc, setViewerSrc] = useState<string | null>(null);
  const [viewerError, setViewerError] = useState<string | null>(null);
  const [isViewerLoading, setIsViewerLoading] = useState(false);
  const [viewerLoadingPaperId, setViewerLoadingPaperId] = useState<
    string | null
  >(null);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState({
    current: 0,
    total: 0,
  });
  const [isDragging, setIsDragging] = useState(false);

  // Dashboard layout states
  const [leftSidebarCollapsed, setLeftSidebarCollapsed] = useState(false);
  const [rightSidebarCollapsed, setRightSidebarCollapsed] = useState(false);
  const [rightSidebarMode, setRightSidebarMode] = useState<
    'entities' | 'graph'
  >('entities');
  const [hoverRightSidebarMode, setHoverRightSidebarMode] = useState<
    'entities' | 'graph' | null
  >(null);
  const [exportOpen, setExportOpen] = useState(false);

  const exportRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const graphRef = useRef<KnowledgeGraphHandle | null>(null);

  // close the export menu on outside click
  useEffect(() => {
    if (!exportOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (exportRef.current && !exportRef.current.contains(e.target as Node)) {
        setExportOpen(false);
      }
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [exportOpen]);

  // Check localStorage queue for papers added from PaperPage
  useEffect(() => {
    const queueKey = 'bloomindex_mypapers_queue';
    const raw = localStorage.getItem(queueKey);
    if (!raw) return;
    try {
      const queued: UploadedPaper[] = JSON.parse(raw);
      if (queued.length > 0) {
        addPapers(queued);
        // Only auto-select if nothing is currently selected.
        if (!selectedPaperId) {
          setSelectedPaperId(queued[0].id);
        }
        localStorage.removeItem(queueKey);
      }
    } catch {
      // Ignore malformed queue
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    return () => {
      if (viewerSrc) {
        URL.revokeObjectURL(viewerSrc);
      }
    };
  }, [viewerSrc]);

  // Auto-load PDF when active paper changes is moved down

  const closeViewer = () => {
    if (viewerSrc) {
      URL.revokeObjectURL(viewerSrc);
    }
    setViewerSrc(null);
    setViewerError(null);
    setIsViewerLoading(false);
    setViewerLoadingPaperId(null);
    setViewerPaper(null);
  };

  const openViewer = async (paper: UploadedPaper) => {
    if (!paper.pdfUrl) return;

    if (viewerSrc) {
      URL.revokeObjectURL(viewerSrc);
      setViewerSrc(null);
    }

    setViewerPaper(paper);
    setViewerError(null);
    setIsViewerLoading(true);
    setViewerLoadingPaperId(paper.id);

    try {
      const res = await fetch(paper.pdfUrl);

      if (!res.ok) {
        const errText = await res.text();
        throw new Error(errText || 'Failed to load PDF preview');
      }

      const blob = await res.blob();
      const objectUrl = URL.createObjectURL(blob);
      setViewerSrc(objectUrl);
    } catch {
      setViewerError('Unable to load this PDF preview.');
    } finally {
      setIsViewerLoading(false);
      setViewerLoadingPaperId(null);
    }
  };

  const deletePaper = async (paper: UploadedPaper) => {
    if (!paper.pdfUrl) {
      // Just remove from store if no backend PDF — the store action
      // also drops it from compareSelection and clears selectedPaperId
      // if this was the selected one.
      removePaper(paper.id);
      return;
    }

    try {
      const storedFilename = paper.pdfUrl.split('/').pop()?.split('?')[0];
      if (storedFilename) {
        // Backend deletion is best-effort; the paper is removed locally regardless.
        await fetch(`/ner/uploaded/${storedFilename}`, {
          method: 'DELETE',
          credentials: 'same-origin',
        });
      }
    } catch {
      // Backend deletion failures are intentionally silent: local removal
      // below is the source of truth for the UI.
    } finally {
      removePaper(paper.id);
    }
  };

  const processFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return;

    const pdfFiles = Array.from(files).filter((f) =>
      f.name.toLowerCase().endsWith('.pdf'),
    );
    if (pdfFiles.length === 0) return;

    setIsUploading(true);
    setUploadProgress({ current: 0, total: pdfFiles.length });
    setIsCompareMode(false);
    clearCompareSelection();

    const uploadedPapers: UploadedPaper[] = [];

    for (let i = 0; i < pdfFiles.length; i++) {
      const file = pdfFiles[i];
      setUploadProgress({ current: i + 1, total: pdfFiles.length });

      try {
        const formData = new FormData();
        formData.append('file', file);
        const res = await fetch('/ner/upload/json', {
          method: 'POST',
          credentials: 'same-origin',
          body: formData,
        });
        if (!res.ok) continue;
        const data = await res.json();

        const paper: UploadedPaper = {
          id: `${Date.now()}_${i}`,
          name: data.metadata.title || file.name,
          doi: data.metadata.doi,
          pdfUrl: data.pdf_url || null,
          entities: data.entities,
          entity_counts: data.entity_counts || {},
          entity_count: data.entity_count,
        };
        uploadedPapers.push(paper);
      } catch {
        // Skip failed uploads
      }
    }

    if (uploadedPapers.length > 0) {
      addPapers(uploadedPapers);
      setSelectedPaperId(uploadedPapers[0].id);
      const initial: Record<string, boolean> = {};
      ENTITY_GROUP_ORDER.forEach((k) => (initial[k] = false));
      setExpandedGroups(initial);
    }

    setIsUploading(false);
    setUploadProgress({ current: 0, total: 0 });
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    processFiles(e.target.files);
    e.target.value = ''; // Reset input so same files can be selected again
  };

  const activePapers = useMemo(() => {
    if (isCompareMode && compareSelection.length >= 2) {
      const selected = new Set(compareSelection);
      return papers.filter((p) => selected.has(p.id));
    }
    return selectedPaper ? [selectedPaper] : [];
  }, [isCompareMode, compareSelection, selectedPaper, papers]);

  const groupedEntities = useMemo(() => {
    if (activePapers.length === 0) return [];

    const mergedCounts: Record<
      string,
      Map<
        string,
        {
          text: string;
          count: number;
          papers: Set<string>;
          canonical: string;
          maxSingleCount: number;
        }
      >
    > = {};

    activePapers.forEach((paper) => {
      Object.entries(paper.entity_counts).forEach(([label, items]) => {
        if (!mergedCounts[label]) mergedCounts[label] = new Map();
        items.forEach((item) => {
          const canonical = item.canonical || item.text;
          const key = canonical.toLowerCase();
          const existing = mergedCounts[label].get(key);
          if (existing) {
            existing.count += item.count;
            existing.papers.add(paper.id);
            // Keep display text from the paper with highest individual count for this canonical
            if (item.count > existing.maxSingleCount) {
              existing.maxSingleCount = item.count;
              existing.text = item.text;
            }
          } else {
            mergedCounts[label].set(key, {
              text: item.text,
              count: item.count,
              papers: new Set([paper.id]),
              canonical,
              maxSingleCount: item.count,
            });
          }
        });
      });
    });

    return ENTITY_GROUP_ORDER.map((label) => {
      const itemsMap = mergedCounts[label];
      if (!itemsMap || itemsMap.size === 0) {
        return {
          label,
          items: [] as { text: string; count: number; papers: Set<string> }[],
          totalCount: 0,
          termCount: 0,
        };
      }
      const sortedItems = Array.from(itemsMap.values()).sort(
        (a, b) => b.count - a.count,
      );
      return {
        label,
        items: sortedItems,
        totalCount: sortedItems.reduce((sum, item) => sum + item.count, 0),
        termCount: sortedItems.length,
      };
    });
  }, [activePapers]);

  const { graphEntities, paperIdentifiers, entityPaperMap } = useMemo(() => {
    if (activePapers.length === 0) {
      return {
        graphEntities: [] as Entity[],
        paperIdentifiers: [] as { type: string; value: string }[],
        entityPaperMap: {} as Record<string, string[]>,
      };
    }

    const allEntities: (Entity & { count: number })[] = [];
    const paperMap: Record<string, string[]> = {};
    const pids: { type: string; value: string }[] = [];

    activePapers.forEach((paper) => {
      pids.push({ type: 'doi', value: paper.doi || paper.id });
      Object.entries(paper.entity_counts).forEach(([label, items]) => {
        items.forEach((item) => {
          allEntities.push({
            text: item.text,
            label,
            score: 1,
            count: item.count,
          });
          const canonical = item.canonical || item.text;
          const key = `${label}-${canonical.toLowerCase()}`;
          if (!paperMap[key]) paperMap[key] = [];
          const paperValue = paper.doi || paper.id;
          if (!paperMap[key].includes(paperValue)) {
            paperMap[key].push(paperValue);
          }
        });
      });
    });

    return {
      graphEntities: allEntities,
      paperIdentifiers: pids,
      entityPaperMap: paperMap,
    };
  }, [activePapers]);

  const exportCSV = () => {
    if (activePapers.length === 0) return;
    const lines = ['Entity Type,Entity Name,Count,Papers'];
    groupedEntities.forEach((group) => {
      group.items.forEach((item) => {
        const paperNames = activePapers
          .filter((p) => item.papers.has(p.id))
          .map((p) => p.name.replace(/"/g, '""'))
          .join('; ');
        lines.push(
          `"${group.label}","${item.text.replace(/"/g, '""')}",${item.count},"${paperNames}"`,
        );
      });
    });
    const csv = lines.join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const filename = isCompareMode
      ? `compared_entities_${activePapers.length}_papers`
      : activePapers[0]?.name.replace(/[^a-zA-Z0-9]/g, '_') || 'entities';
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${filename}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  const exportGraph = () => {
    if (activePapers.length === 0) return;
    // Exact copy when the live graph is mounted; plain fallback otherwise.
    if (graphRef.current?.exportSnapshot()) {
      return;
    }
    const nodes: { id: string; label: string; group: string }[] =
      graphEntities.map((e) => ({
        id: `${e.label}-${e.text.toLowerCase()}`,
        label: e.text,
        group: e.label,
      }));
    const edges: { from: string; to: string }[] = [];
    activePapers.forEach((paper) => {
      const pid = `paper-${paper.doi || paper.id}`;
      nodes.push({ id: pid, label: paper.name, group: 'PAPER' });
      Object.entries(paper.entity_counts).forEach(([label, items]) => {
        items.forEach((item) => {
          edges.push({
            from: pid,
            to: `${label}-${(item.canonical || item.text).toLowerCase()}`,
          });
        });
      });
    });

    // standalone offline HTML export (no CDN), shared helper.
    const title = isCompareMode
      ? `${activePapers.length} Papers`
      : activePapers[0]?.name || 'Graph';
    void downloadGraphHtml({
      nodes,
      edges,
      filename: `graph_export.html`,
      title: `Knowledge Graph - ${title}`,
      subtitle: `Entities linked to ${title}`,
    });
  };

  const entityConfig: Record<string, { accentVar: string }> = {};
  ENTITY_GROUP_ORDER.forEach((label) => {
    entityConfig[label] = { accentVar: getEntityAccentVar(label) };
  });

  const isComparing = isCompareMode && compareSelection.length >= 2;

  // Auto-load PDF when active paper changes. Fires an async side effect
  // (PDF load), so it must stay in the effect — render cannot do this
  // (per-call disable below).
  useEffect(() => {
    if (!isCompareMode && activePapers.length === 1) {
      const paper = activePapers[0];
      if (
        paper.pdfUrl &&
        paper.id !== viewerPaper?.id &&
        paper.id !== viewerLoadingPaperId
      ) {
        // eslint-disable-next-line react-hooks/set-state-in-effect -- async side effect
        void openViewer(paper);
      } else if (!paper.pdfUrl) {
        closeViewer();
      }
    } else if (isCompareMode) {
      closeViewer();
    }
  }, [activePapers, isCompareMode, viewerPaper?.id, viewerLoadingPaperId]);

  return (
    <div
      className="flex h-full w-full bg-background text-on-background overflow-hidden flex-col"
      style={{ fontFamily: 'var(--font-google-sans)' }}
    >
      <main className="flex flex-1 overflow-hidden">
        {/* Left Sidebar: PDF Library & Actions */}
        {papers.length > 0 && (
          <aside
            className={`sidebar-transition border-r border-outline-variant bg-surface-bright flex flex-col shrink-0 relative ${leftSidebarCollapsed ? 'sidebar-collapsed' : 'w-72'}`}
            id="library-sidebar"
          >
            {/* Unified Top Header Bar */}
            <div className="px-3.5 h-14 border-b border-outline-variant/40 flex items-center justify-between shrink-0">
              <span
                className={`!text-[17px] !font-bold text-slate-900 tracking-tight whitespace-nowrap transition-opacity duration-150 ${leftSidebarCollapsed ? 'hidden' : 'block'}`}
              >
                Sources
              </span>
              <button
                className={`p-1.5 text-slate-600 hover:text-slate-900 rounded-md hover:bg-surface-low active:scale-90 transition-all duration-100 outline-none ${leftSidebarCollapsed ? 'mx-auto' : ''}`}
                onClick={() => setLeftSidebarCollapsed(!leftSidebarCollapsed)}
              >
                <SidebarSimple size={20} />
              </button>
            </div>

            {/* Mini View (Icons only) */}
            <div className="sidebar-mini-view flex-col items-center pt-3 gap-2.5 h-full w-full overflow-y-auto custom-scrollbar pb-6">
              {/* Mini Action Buttons */}
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                style={{
                  backgroundColor: '#ffecf6',
                  color: '#d63384',
                  borderColor: '#fbcfe8',
                  boxShadow: 'none',
                }}
                className="w-10 h-10 rounded-xl border flex items-center justify-center transition-all hover:opacity-90 hover:border-[#d63384] text-[#d63384] shadow-none outline-none"
                title={
                  isUploading
                    ? `Extracting ${uploadProgress.current}/${uploadProgress.total}`
                    : 'Add Sources'
                }
              >
                {isUploading ? (
                  <SpinnerGap
                    size={20}
                    weight="bold"
                    className="animate-spin text-[#d63384]"
                  />
                ) : (
                  <Plus size={20} weight="bold" className="text-[#d63384]" />
                )}
              </button>

              {papers.length >= 2 && (
                <button
                  type="button"
                  onClick={() => {
                    if (isCompareMode) {
                      setIsCompareMode(false);
                      clearCompareSelection();
                    } else {
                      setIsCompareMode(true);
                      if (selectedPaper)
                        setCompareSelection([selectedPaper.id]);
                    }
                  }}
                  style={{ boxShadow: 'none' }}
                  className={`w-10 h-10 rounded-xl border transition-all flex items-center justify-center shadow-none outline-none ${
                    isCompareMode
                      ? 'bg-red-50 border-red-200 text-red-600 hover:bg-red-100/70'
                      : 'bg-white border-[#e0e0e0] text-[#333333] hover:bg-[#f4f4f4]'
                  }`}
                  title={isCompareMode ? 'Cancel' : 'Compare'}
                >
                  {isCompareMode ? (
                    <X size={20} weight="bold" className="text-red-600" />
                  ) : (
                    <ChartBar size={20} weight="bold" />
                  )}
                </button>
              )}

              {/* Divider before paper stack */}
              <div className="w-6 h-[1.5px] bg-[#e0e0e0] rounded-full mx-auto my-1.5" />

              {papers.map((paper) => {
                const isSelected = selectedPaper?.id === paper.id;
                const isInCompare = compareSelection.includes(paper.id);
                const active =
                  (isSelected && !isCompareMode) ||
                  (isInCompare && isCompareMode);
                return (
                  <button
                    key={paper.id}
                    title={paper.name}
                    onClick={() => {
                      if (isCompareMode) {
                        toggleCompareSelection(paper.id);
                      } else {
                        setSelectedPaperId(paper.id);
                        const initial: Record<string, boolean> = {};
                        ENTITY_GROUP_ORDER.forEach((k) => (initial[k] = false));
                        setExpandedGroups(initial);
                      }
                    }}
                    className={`w-11 h-11 rounded-2xl flex items-center justify-center transition-all ${active ? 'bg-[#f4f4f4] opacity-100' : 'hover:bg-[#fafafa] opacity-70 hover:opacity-100'}`}
                  >
                    <PdfIcon size={26} />
                  </button>
                );
              })}
            </div>

            {/* Main Sidebar Content */}
            <div
              className={`sidebar-content h-full flex flex-col overflow-hidden w-72 transition-opacity duration-150 ${leftSidebarCollapsed ? 'opacity-0 pointer-events-none' : 'opacity-100'}`}
            >
              <div className="pt-3 space-y-2 px-4 pb-3">
                <label className="block relative">
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".pdf"
                    multiple
                    onChange={handleFileChange}
                    className="hidden"
                  />
                  <div
                    style={{
                      backgroundColor: '#ffecf6',
                      color: '#d63384',
                      borderColor: '#fbcfe8',
                      fontFamily: 'var(--font-google-sans)',
                      boxShadow: 'none',
                    }}
                    className="w-full py-2.5 px-4 rounded-full border flex items-center justify-center gap-2 text-[14.5px] font-bold transition-all hover:opacity-90 active:scale-[0.99] cursor-pointer text-[#d63384] shadow-none outline-none"
                  >
                    {isUploading ? (
                      <SpinnerGap
                        size={18}
                        weight="bold"
                        className="animate-spin text-[#d63384]"
                      />
                    ) : (
                      <Plus
                        size={18}
                        weight="bold"
                        className="text-[#d63384]"
                      />
                    )}
                    <span className="font-bold text-[#d63384]">
                      {isUploading
                        ? `Extracting ${uploadProgress.current}/${uploadProgress.total}`
                        : 'Add Sources'}
                    </span>
                  </div>
                </label>

                {papers.length >= 2 && (
                  <button
                    type="button"
                    onClick={() => {
                      if (isCompareMode) {
                        setIsCompareMode(false);
                        clearCompareSelection();
                      } else {
                        setIsCompareMode(true);
                        if (selectedPaper)
                          setCompareSelection([selectedPaper.id]);
                      }
                    }}
                    style={{
                      fontFamily: 'var(--font-google-sans)',
                      boxShadow: 'none',
                    }}
                    className={`w-full py-2.5 px-4 rounded-full flex items-center justify-center gap-2 text-[14.5px] transition-all border active:scale-[0.99] shadow-none outline-none ${
                      isCompareMode
                        ? 'bg-red-50 border-red-200 text-red-600 font-semibold hover:bg-red-100/70'
                        : 'bg-white border-slate-200 text-slate-800 hover:bg-slate-50 hover:border-slate-300 font-semibold'
                    }`}
                  >
                    {isCompareMode ? (
                      <X size={18} weight="bold" className="text-red-600" />
                    ) : (
                      <ChartBar
                        size={18}
                        weight="bold"
                        className="text-slate-700"
                      />
                    )}
                    <span>{isCompareMode ? 'Cancel' : 'Compare'}</span>
                  </button>
                )}
              </div>

              {/* Subtle divider before paper stack in uncollapsed state */}
              <div className="mx-4 mb-2.5 h-[1px] bg-slate-200/60 shrink-0" />

              <div className="flex-1 overflow-y-auto custom-scrollbar px-3 pb-6 space-y-1.5">
                {papers.map((paper) => {
                  const isSelected = selectedPaper?.id === paper.id;
                  const isInCompare = compareSelection.includes(paper.id);
                  const active =
                    (isSelected && !isCompareMode) ||
                    (isInCompare && isCompareMode);

                  return (
                    <div
                      key={paper.id}
                      onClick={() => {
                        if (isCompareMode) {
                          toggleCompareSelection(paper.id);
                        } else {
                          setSelectedPaperId(paper.id);
                          const initial: Record<string, boolean> = {};
                          ENTITY_GROUP_ORDER.forEach(
                            (k) => (initial[k] = false),
                          );
                          setExpandedGroups(initial);
                        }
                      }}
                      className={`p-3 relative group cursor-pointer transition-all flex items-center gap-3 rounded-2xl ${active ? 'bg-[#f4f4f4] opacity-100' : 'bg-transparent hover:bg-[#fafafa] opacity-70 hover:opacity-100'}`}
                    >
                      <PdfIcon size={24} className="shrink-0" />
                      <div className="flex-1 min-w-0 pr-6">
                        <h4
                          className={`text-[14px] line-clamp-2 leading-tight ${active ? 'font-bold text-slate-900' : 'font-medium text-slate-700'}`}
                        >
                          {paper.name}
                        </h4>
                      </div>

                      {/* Delete Menu Trigger */}
                      <button
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation();
                          void deletePaper(paper);
                        }}
                        className="absolute right-3 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 p-1 text-slate-400 hover:text-red-500 transition-colors"
                        title="Delete"
                      >
                        <TrashSimple size={16} weight="regular" />
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>
          </aside>
        )}

        {/* Center Viewport: Document Reader or Upload Hero */}
        <section className="flex-1 w-full h-full bg-surface-dim overflow-y-auto custom-scrollbar flex flex-col items-center justify-center relative">
          {papers.length === 0 ? (
            <div className="flex-1 relative dot-pattern flex flex-col items-center justify-center overflow-hidden w-full h-full bg-background">
              {/* Abstract Background Shapes */}
              <div className="absolute top-[20%] left-[10%] w-64 h-64 bg-[#ff6dba] opacity-10 blur-[100px] rounded-full pointer-events-none" />
              <div className="absolute bottom-[20%] right-[10%] w-96 h-96 bg-[#ff85c8] opacity-10 blur-[120px] rounded-full pointer-events-none" />

              <div className="relative z-10 w-full max-w-2xl px-6">
                <label
                  onDragOver={(e) => {
                    e.preventDefault();
                    setIsDragging(true);
                  }}
                  onDragLeave={() => setIsDragging(false)}
                  onDrop={(e) => {
                    e.preventDefault();
                    setIsDragging(false);
                    processFiles(e.dataTransfer.files);
                  }}
                  className={`
                    relative w-full aspect-[1.6/1] rounded-3xl
                    flex flex-col items-center justify-center
                    transition-all duration-500
                    ${
                      isUploading
                        ? 'bg-white/90 border border-[#ff6dba]/30 shadow-[0_4px_30px_rgba(255,109,186,0.18),_0_0_60px_rgba(255,109,186,0.12)] cursor-default pointer-events-none'
                        : isDragging
                          ? 'bg-[#ffecf6]/50 scale-[1.01] cursor-pointer'
                          : 'bg-white/70 hover:bg-[#ffecf6]/30 cursor-pointer group hover:scale-[1.005]'
                    }
                  `}
                >
                  {/* Thick, long-dash border in signature light pink (Upload state only) */}
                  <svg
                    className={`
                      absolute inset-0 w-full h-full pointer-events-none overflow-visible rounded-3xl
                      transition-opacity duration-500
                      ${isUploading ? 'opacity-0 pointer-events-none' : 'opacity-100'}
                    `}
                    xmlns="http://www.w3.org/2000/svg"
                  >
                    <rect
                      x="1.5"
                      y="1.5"
                      style={{
                        width: 'calc(100% - 3px)',
                        height: 'calc(100% - 3px)',
                      }}
                      rx="24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="3"
                      strokeDasharray="20 12"
                      className={`
                        transition-colors duration-300
                        ${
                          isDragging
                            ? 'text-[#ff6dba]'
                            : 'text-[#ff6dba]/50 group-hover:text-[#ff6dba]'
                        }
                      `}
                    />
                  </svg>

                  <input
                    type="file"
                    accept=".pdf"
                    multiple
                    onChange={handleFileChange}
                    className="hidden"
                  />

                  {!isUploading ? (
                    <div className="flex flex-col items-center transition-all duration-300 relative z-10 text-center px-6">
                      <div className="mb-5 group-hover:scale-110 transition-transform duration-300">
                        <FileArrowUp
                          size={82}
                          weight="regular"
                          className="text-[#ff6dba]"
                        />
                      </div>
                      <h1
                        className="text-[28px] font-bold mb-5 tracking-tight text-[#ff6dba]"
                        style={{ fontFamily: 'var(--font-google-sans)' }}
                      >
                        Upload
                      </h1>
                      <p
                        className="text-[15px] text-on-surface-variant text-center"
                        style={{ fontFamily: 'var(--font-google-sans)' }}
                      >
                        Analyse papers and extract structured entities
                        automatically.
                      </p>
                    </div>
                  ) : (
                    <div className="flex flex-col items-center transition-all duration-300 relative z-10 text-center px-6 w-full">
                      <div className="mb-5">
                        <SpinnerGap
                          size={52}
                          className="text-slate-900 animate-spin"
                        />
                      </div>
                      <h2
                        className="text-[26px] font-bold mb-3 tracking-tight text-slate-900"
                        style={{ fontFamily: 'var(--font-google-sans)' }}
                      >
                        Extracting...
                      </h2>
                      <p
                        className="text-[15px] font-bold text-on-surface-variant mb-6 text-center tracking-wide"
                        style={{ fontFamily: 'var(--font-google-sans)' }}
                      >
                        {uploadProgress.current}/{uploadProgress.total}
                      </p>
                      <div className="w-full max-w-xs h-[3px] bg-[#ffecf6] rounded-full overflow-hidden relative">
                        <div
                          className="
                            absolute inset-0 rounded-full
                            bg-gradient-to-r from-transparent via-[#ff6dba] to-transparent
                            shadow-[0_0_12px_rgba(255,109,186,0.8)]
                            animate-pulse pointer-events-none
                          "
                        />
                        <div
                          className="absolute inset-y-0 w-2/5 bg-gradient-to-r from-transparent via-[#ff6dba] to-transparent"
                          style={{
                            animation:
                              'pq-trail-slide 1.4s cubic-bezier(0.4, 0, 0.2, 1) infinite',
                            filter:
                              'drop-shadow(0 0 6px rgba(255, 109, 186, 0.9))',
                          }}
                        />
                      </div>
                    </div>
                  )}
                </label>
              </div>
            </div>
          ) : activePapers.length > 0 ? (
            <>
              {/* Doc Header Toolbar */}
              <div className="w-full h-14 bg-surface-bright border-b border-outline-variant/40 px-6 sticky top-0 z-40 flex items-center justify-between shrink-0">
                <div className="flex items-center gap-4 flex-1 min-w-0">
                  {isCompareMode ? (
                    <h3 className="font-semibold text-on-surface text-[15px] sm:text-base">
                      Comparing {activePapers.length} Papers
                    </h3>
                  ) : (
                    <div className="flex flex-col flex-1 min-w-0 pr-4">
                      <h3
                        className="font-semibold text-on-surface text-[14px] sm:text-[15px] leading-tight break-words line-clamp-1"
                        title={activePapers[0]?.name}
                      >
                        {activePapers[0]?.name}
                      </h3>
                      {activePapers[0]?.doi && (
                        <span className="font-mono text-[10.5px] text-blue-600 hover:text-blue-800 font-medium">
                          {activePapers[0].doi}
                        </span>
                      )}
                    </div>
                  )}
                </div>
                <div className="flex items-center gap-4">
                  {/* Export removed from here, now in right sidebar */}
                </div>
              </div>

              {/* Main Content Area */}
              <div className="flex-1 w-full flex flex-col min-h-0">
                {isCompareMode && activePapers.length >= 2 ? (
                  <CompareMatrix papers={activePapers} />
                ) : (
                  <div className="flex-1 bg-white border border-outline-variant overflow-hidden flex flex-col w-full h-[min(85vh,1200px)] relative">
                    {isViewerLoading ? (
                      <div
                        className="absolute inset-0 flex flex-col items-center justify-center bg-surface-c/50 backdrop-blur-sm z-10 gap-3.5"
                        style={{ fontFamily: 'var(--font-google-sans)' }}
                      >
                        <SpinnerGap
                          size={46}
                          className="animate-spin text-slate-900"
                        />
                        <span className="text-[17px] text-on-surface-variant font-medium">
                          Loading...
                        </span>
                      </div>
                    ) : viewerError ? (
                      <div className="absolute inset-0 flex items-center justify-center bg-red-50 text-red-600 text-sm font-medium">
                        {viewerError}
                      </div>
                    ) : null}
                    <iframe
                      src={viewerSrc || undefined}
                      title={`PDF viewer`}
                      className="w-full h-full border-none"
                    />
                  </div>
                )}
              </div>
            </>
          ) : (
            <div
              className="flex-1 flex items-center justify-center text-center px-6"
              style={{ fontFamily: 'var(--font-google-sans)' }}
            >
              <p className="text-[19px] md:text-[21px] italic font-normal text-on-surface-variant/70">
                {isCompareMode
                  ? 'Select at least 2 papers to compare'
                  : 'Select a paper to view entities'}
              </p>
            </div>
          )}
        </section>

        {/* Right Sidebar: Insights & Entity Index */}
        {activePapers.length > 0 && (
          <aside
            className={`sidebar-transition border-l border-outline-variant bg-surface flex flex-col shrink-0 relative ${rightSidebarCollapsed ? 'sidebar-collapsed' : 'w-[400px]'}`}
            id="insights-sidebar"
          >
            {/* Integrated Sidebar Toggle (Top Left) */}
            <button
              className="toggle-btn-right absolute left-4 top-3 z-50 p-1.5 text-on-surface-variant rounded-full flex items-center justify-center hover:bg-surface-low active:scale-90 transition-all duration-100 outline-none focus:outline-none focus:ring-0"
              onClick={() => setRightSidebarCollapsed(!rightSidebarCollapsed)}
            >
              <SidebarSimple size={20} className="rotate-180" />
            </button>

            {/* Mini View (Icons only) */}
            <div
              className={`sidebar-mini-view ${rightSidebarCollapsed ? 'flex opacity-100' : 'hidden opacity-0 pointer-events-none'} flex-col items-center pt-16 gap-6 h-full w-full transition-opacity duration-150`}
            >
              {!isCompareMode && (
                <button
                  onClick={() => {
                    setRightSidebarMode('entities');
                    setRightSidebarCollapsed(false);
                  }}
                  className={`p-2 rounded-lg active:scale-90 transition-all duration-100 ${
                    rightSidebarMode === 'entities'
                      ? 'text-black bg-[#f4f4f4]'
                      : 'text-[#666666] hover:text-black hover:bg-[#fafafa]'
                  }`}
                  title="Entity Index"
                >
                  <ListBullets
                    size={22}
                    weight={
                      rightSidebarMode === 'entities' ? 'bold' : 'regular'
                    }
                  />
                </button>
              )}
              <button
                onClick={() => {
                  setRightSidebarMode('graph');
                  setRightSidebarCollapsed(false);
                }}
                className={`p-2 rounded-lg active:scale-90 transition-all duration-100 ${
                  rightSidebarMode === 'graph' || isCompareMode
                    ? 'text-black bg-[#f4f4f4]'
                    : 'text-[#666666] hover:text-black hover:bg-[#fafafa]'
                }`}
                title="Graph View"
              >
                <Graph
                  size={22}
                  weight={
                    rightSidebarMode === 'graph' || isCompareMode
                      ? 'bold'
                      : 'regular'
                  }
                />
              </button>
            </div>

            {/* Main Sidebar Content */}
            <div
              className={`sidebar-content h-full flex flex-col overflow-hidden w-[400px] transition-opacity duration-150 ${rightSidebarCollapsed ? 'opacity-0 pointer-events-none' : 'opacity-100'}`}
            >
              {/* Centered view switcher pill header */}
              <div
                style={{
                  position: 'relative',
                  display: 'flex',
                  justifyContent: 'center',
                  minHeight: 44,
                  marginBottom: 26,
                  marginTop: 56,
                  paddingLeft: 28,
                  paddingRight: 28,
                }}
              >
                {!isCompareMode ? (
                  <div
                    style={{
                      display: 'inline-flex',
                      gap: 3,
                      padding: 4,
                      borderRadius: 999,
                      border: 'none',
                      background: 'var(--surface-c)',
                    }}
                  >
                    {(
                      [
                        {
                          id: 'entities',
                          icon: ListBullets,
                          label: 'Entity Index',
                        },
                        { id: 'graph', icon: Graph, label: 'Graph View' },
                      ] as const
                    ).map(({ id, icon: Icon, label }) => {
                      const isActive = rightSidebarMode === id;
                      const expanded = hoverRightSidebarMode === id;
                      return (
                        <button
                          key={id}
                          data-tab={id}
                          onClick={() => setRightSidebarMode(id)}
                          onMouseEnter={() => setHoverRightSidebarMode(id)}
                          onMouseLeave={() => setHoverRightSidebarMode(null)}
                          style={{
                            display: 'inline-flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            gap: 7,
                            height: 36,
                            borderRadius: 999,
                            width: expanded ? 'auto' : 40,
                            padding: expanded ? '0 14px' : 0,
                            background: isActive
                              ? 'var(--background, #FFFFFF)'
                              : 'transparent',
                            boxShadow: isActive
                              ? '0 1px 3px rgba(0,0,0,.08)'
                              : 'none',
                            border: 'none',
                            cursor: 'pointer',
                            fontSize: 13.5,
                            fontWeight: isActive ? 600 : 500,
                            fontFamily: 'var(--font-google-sans)',
                            color: isActive
                              ? 'var(--on-surface)'
                              : 'var(--on-surface-variant)',
                            whiteSpace: 'nowrap',
                            overflow: 'hidden',
                            transition:
                              'width .22s ease, padding .22s ease, background .15s, color .15s',
                          }}
                        >
                          {expanded ? (
                            <span
                              className="inline-flex items-center gap-2"
                              style={{ fontFamily: 'var(--font-google-sans)' }}
                            >
                              <Icon
                                size={18}
                                weight={isActive ? 'bold' : 'regular'}
                              />
                              <span
                                style={{
                                  fontSize: 13.5,
                                  fontWeight: isActive ? 600 : 500,
                                }}
                              >
                                {label}
                              </span>
                            </span>
                          ) : (
                            <Icon
                              size={19}
                              weight={isActive ? 'bold' : 'regular'}
                            />
                          )}
                        </button>
                      );
                    })}
                  </div>
                ) : (
                  <div
                    className="flex items-center justify-center"
                    style={{ height: 36 }}
                  >
                    <span
                      style={{
                        fontSize: 17,
                        fontWeight: 600,
                        fontFamily: 'var(--font-google-sans)',
                        color: 'var(--on-surface)',
                        letterSpacing: 'normal',
                      }}
                    >
                      Graph View
                    </span>
                  </div>
                )}

                {/* Export options pinned right (single paper mode only) */}
                {!isCompareMode && (
                  <div
                    ref={exportRef}
                    style={{ position: 'absolute', right: 28, top: 2 }}
                  >
                    <button
                      onClick={() => setExportOpen((o) => !o)}
                      title="Export"
                      aria-label="Export"
                      style={{
                        width: 40,
                        height: 40,
                        borderRadius: 999,
                        background: exportOpen
                          ? 'var(--surface-c)'
                          : 'transparent',
                        color: 'var(--on-surface-variant)',
                        border: 'none',
                        cursor: 'pointer',
                        display: 'grid',
                        placeItems: 'center',
                        transition: 'background .15s',
                      }}
                    >
                      <DotsThreeVertical size={20} weight="bold" />
                    </button>
                    {exportOpen && (
                      <div
                        style={{
                          position: 'absolute',
                          top: 'calc(100% + 4px)',
                          right: 0,
                          width: 'max-content',
                          background: '#FFFFFF',
                          border: '1px solid var(--border)',
                          borderRadius: 12,
                          boxShadow: '0 2px 6px 2px rgba(0, 0, 0, 0.08)',
                          padding: 5,
                          zIndex: 100,
                          fontFamily: 'var(--font-google-sans)',
                          animation: 'fadeUp .16s ease',
                        }}
                      >
                        <button
                          className="export-opt"
                          style={{ fontFamily: 'var(--font-google-sans)' }}
                          onClick={() => {
                            setExportOpen(false);
                            exportCSV();
                          }}
                        >
                          <span
                            style={{ fontFamily: 'var(--font-google-sans)' }}
                          >
                            Export CSV
                          </span>
                        </button>
                        <button
                          className="export-opt"
                          style={{ fontFamily: 'var(--font-google-sans)' }}
                          onClick={() => {
                            setExportOpen(false);
                            exportGraph();
                          }}
                        >
                          <span
                            style={{ fontFamily: 'var(--font-google-sans)' }}
                          >
                            Export Graph
                          </span>
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Entity Index + Graph share one anchored scroll region, so switching
                  tabs or expanding groups never moves surrounding content */}
              <div className="flex-1 min-h-0 overflow-y-auto scrollbar-hide px-8">
                {!isCompareMode && rightSidebarMode === 'entities' && (
                  <>
                    <div
                      className="pb-8"
                      style={{ display: 'flex', flexDirection: 'column' }}
                    >
                      {groupedEntities.map((group) => {
                        const isExpanded =
                          expandedGroups[group.label] !== false;
                        const accentColor = getEntityAccentColor(group.label);
                        const empty = group.termCount === 0;

                        return (
                          <div
                            key={group.label}
                            style={{ borderBottom: '1px solid var(--border)' }}
                          >
                            {/* Accordion Row (.ent-cat) */}
                            <button
                              type="button"
                              className="ent-cat group"
                              onClick={
                                empty
                                  ? undefined
                                  : () => {
                                      toggleGroup(group.label);
                                    }
                              }
                              style={{
                                display: 'flex',
                                alignItems: 'center',
                                gap: 12,
                                width: '100%',
                                padding: '13px 4px',
                                background: 'transparent',
                                border: 'none',
                                cursor: empty ? 'default' : 'pointer',
                                opacity: empty ? 0.5 : 1,
                              }}
                            >
                              {/* Left category accent dot bar */}
                              <span
                                style={{
                                  width: 4,
                                  height: 20,
                                  borderRadius: 999,
                                  background: accentColor,
                                  flexShrink: 0,
                                  opacity: empty ? 0.3 : 1,
                                }}
                              />

                              <span
                                style={{
                                  flex: 1,
                                  textAlign: 'left',
                                  fontSize: 14,
                                  fontWeight: 600,
                                  color: isExpanded
                                    ? accentColor
                                    : 'var(--on-surface)',
                                  transition: 'color .15s',
                                  textTransform: 'capitalize',
                                }}
                              >
                                {LABEL_NAMES[group.label] ||
                                  group.label.toLowerCase()}
                              </span>

                              {/* Hover caret transitions slot */}
                              <span style={{ flexShrink: 0 }}>
                                {empty ? (
                                  <span
                                    style={{
                                      fontSize: 14,
                                      color: 'var(--on-surface-variant)',
                                    }}
                                  >
                                    –
                                  </span>
                                ) : isExpanded ? (
                                  <span className="relative flex items-center justify-center min-w-[24px] h-[20px] text-on-surface-variant">
                                    <CaretUp size={16} weight="bold" />
                                  </span>
                                ) : (
                                  <span className="relative flex items-center justify-center min-w-[24px] h-[20px]">
                                    {/* Entity Count: visible without cursor, fades out on hover */}
                                    <span className="transition-all duration-200 text-[13px] font-semibold text-on-surface-variant group-hover:opacity-0 group-hover:scale-75">
                                      {group.termCount}
                                    </span>

                                    {/* Caret: animates into view on hover */}
                                    <span className="absolute inset-0 m-auto flex items-center justify-center transition-all duration-200 text-on-surface-variant opacity-0 scale-75 group-hover:opacity-100 group-hover:scale-100">
                                      <CaretDown size={16} weight="bold" />
                                    </span>
                                  </span>
                                )}
                              </span>
                            </button>

                            {/* Values expanded container — animated open/close */}
                            {!empty && (
                              <div
                                className="grid transition-[grid-template-rows,opacity] duration-300 ease-out"
                                style={{
                                  gridTemplateRows: isExpanded ? '1fr' : '0fr',
                                  opacity: isExpanded ? 1 : 0,
                                }}
                              >
                                <div className="min-h-0 overflow-hidden">
                                  <div
                                    style={{
                                      padding: '0 4px 12px 16px',
                                      display: 'flex',
                                      flexDirection: 'column',
                                      gap: 2,
                                    }}
                                  >
                                    {group.items.map((ent, eIdx) => {
                                      const name = ent.text;
                                      return (
                                        <div
                                          key={eIdx}
                                          className="ent-row"
                                          style={{
                                            display: 'flex',
                                            alignItems: 'center',
                                            gap: 10,
                                            padding: '6px 10px',
                                            borderRadius: 7,
                                            cursor: 'default',
                                            background: 'transparent',
                                          }}
                                        >
                                          {/* Item dot */}
                                          <span
                                            style={{
                                              width: 7,
                                              height: 7,
                                              borderRadius: 999,
                                              background: accentColor,
                                              flexShrink: 0,
                                            }}
                                          />

                                          {/* Item name */}
                                          <span
                                            style={{
                                              flex: 1,
                                              fontSize: 13.5,
                                              color: 'var(--on-surface)',
                                              fontStyle:
                                                group.label === 'SPECIES'
                                                  ? 'italic'
                                                  : 'normal',
                                            }}
                                          >
                                            {name}
                                          </span>

                                          <span
                                            style={{
                                              fontSize: 12,
                                              color:
                                                'var(--on-surface-variant)',
                                              fontVariantNumeric:
                                                'tabular-nums',
                                            }}
                                          >
                                            {ent.count}
                                          </span>
                                        </div>
                                      );
                                    })}
                                  </div>
                                </div>
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </>
                )}
                {/* Always mounted (hidden when inactive) so Export Graph snapshots the live view from any tab */}
                <div
                  className="flex flex-col pb-8"
                  style={{
                    display:
                      !isCompareMode && rightSidebarMode === 'entities'
                        ? 'none'
                        : undefined,
                  }}
                >
                  <div className="w-[320px] mx-auto">
                    <KnowledgeGraph
                      ref={graphRef}
                      active={isCompareMode || rightSidebarMode === 'graph'}
                      entities={graphEntities}
                      paperIdentifier={
                        isComparing
                          ? undefined
                          : { type: 'doi', value: activePapers[0]?.doi || '' }
                      }
                      paperIdentifiers={
                        isComparing ? paperIdentifiers : undefined
                      }
                      entityConfig={entityConfig}
                      entityPaperMap={isComparing ? entityPaperMap : undefined}
                    />
                  </div>
                </div>
              </div>
            </div>
          </aside>
        )}
      </main>
    </div>
  );
};

export default AnalysePage;
