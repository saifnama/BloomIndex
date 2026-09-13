/**
 * Analyse page state persisted across navigation via sessionStorage.
 *
 * Preserves uploaded paper metadata and comparison selections per tab.
 * PDF blob URLs remain component-local to avoid leaking memory and
 * storing non-serializable references in browser storage.
 */

import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

export interface UploadedPaper {
  id: string;
  name: string;
  doi?: string;
  pdfUrl?: string | null;
  entities: Record<string, string[]>;
  entity_counts: Record<
    string,
    { text: string; count: number; canonical?: string; aliases?: string[] }[]
  >;
  entity_count: number;
}

interface AnalyseState {
  papers: UploadedPaper[];
  selectedPaperId: string | null;
  expandedGroups: Record<string, boolean>;
  isCompareMode: boolean;
  compareSelection: string[];

  /**
   * Prepends uploaded documents to maintain reverse-chronological order.
   */
  addPapers: (papers: UploadedPaper[]) => void;
  removePaper: (id: string) => void;
  setSelectedPaperId: (id: string | null) => void;
  setExpandedGroups: (groups: Record<string, boolean>) => void;
  toggleGroup: (label: string) => void;
  setIsCompareMode: (compareMode: boolean) => void;
  setCompareSelection: (ids: string[]) => void;
  toggleCompareSelection: (id: string) => void;
  clearCompareSelection: () => void;
  resetAnalyse: () => void;
}

const INITIAL: Pick<
  AnalyseState,
  | 'papers'
  | 'selectedPaperId'
  | 'expandedGroups'
  | 'isCompareMode'
  | 'compareSelection'
> = {
  papers: [],
  selectedPaperId: null,
  expandedGroups: {},
  isCompareMode: false,
  compareSelection: [],
};

export const useAnalyseStore = create<AnalyseState>()(
  persist(
    (set) => ({
      ...INITIAL,

      addPapers: (newPapers) =>
        set((state) => ({ papers: [...newPapers, ...state.papers] })),

      removePaper: (id) =>
        set((state) => ({
          papers: state.papers.filter((p) => p.id !== id),
          selectedPaperId:
            state.selectedPaperId === id ? null : state.selectedPaperId,
          compareSelection: state.compareSelection.filter((s) => s !== id),
        })),

      setSelectedPaperId: (id) => set({ selectedPaperId: id }),

      setExpandedGroups: (groups) => set({ expandedGroups: groups }),

      toggleGroup: (label) =>
        set((state) => ({
          expandedGroups: {
            ...state.expandedGroups,
            [label]: !state.expandedGroups[label],
          },
        })),

      setIsCompareMode: (compareMode) => set({ isCompareMode: compareMode }),

      setCompareSelection: (ids) => set({ compareSelection: ids }),

      toggleCompareSelection: (id) =>
        set((state) => ({
          compareSelection: state.compareSelection.includes(id)
            ? state.compareSelection.filter((s) => s !== id)
            : [...state.compareSelection, id],
        })),

      clearCompareSelection: () => set({ compareSelection: [] }),

      resetAnalyse: () => set(INITIAL),
    }),
    {
      name: 'bi_analyse_state',
      storage: createJSONStorage(() => sessionStorage),
      partialize: (state) => ({
        papers: state.papers,
        selectedPaperId: state.selectedPaperId,
        expandedGroups: state.expandedGroups,
        isCompareMode: state.isCompareMode,
        compareSelection: state.compareSelection,
      }),
    },
  ),
);
