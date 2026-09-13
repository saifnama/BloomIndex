/**
 * Search page cache persisted across route navigation in sessionStorage.
 *
 * Provides a warm cache for returned literature query results when
 * navigating back to the search view. Transient UI states such as
 * loading spinners and network errors remain unpersisted.
 */

import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import type { SearchFilters, SearchResult } from '../types';

export interface SearchPagination {
  total: number;
  page: number;
  hasMore: boolean;
  pageSize: number;
}

interface SearchState {
  results: SearchResult[];
  pagination: SearchPagination | null;
  currentPage: number;
  lastQuery: string;
  lastFilters: SearchFilters | null;
  /**
   * Tracks container scroll depth to restore position upon return.
   */
  scrollY: number;

  /**
   * Atomically updates query parameters, filters, and paginated results.
   */
  setSearchResult: (args: {
    results: SearchResult[];
    pagination: SearchPagination | null;
    currentPage: number;
    lastQuery: string;
    lastFilters: SearchFilters;
  }) => void;
  setScrollY: (y: number) => void;
  /**
   * Resets cached search criteria and paginated records to defaults.
   */
  resetSearch: () => void;
}

const INITIAL: Omit<
  SearchState,
  'setSearchResult' | 'setScrollY' | 'resetSearch'
> = {
  results: [],
  pagination: null,
  currentPage: 1,
  lastQuery: '',
  lastFilters: null,
  scrollY: 0,
};

export const useSearchStore = create<SearchState>()(
  persist(
    (set) => ({
      ...INITIAL,

      setSearchResult: ({
        results,
        pagination,
        currentPage,
        lastQuery,
        lastFilters,
      }) =>
        set({
          results,
          pagination,
          currentPage,
          lastQuery,
          lastFilters,
        }),

      setScrollY: (y) => set({ scrollY: y }),

      resetSearch: () => set(INITIAL),
    }),
    {
      name: 'bi_search_state',
      storage: createJSONStorage(() => sessionStorage),
      // Filters non-serializable properties out of sessionStorage.
      partialize: (state) => ({
        results: state.results,
        pagination: state.pagination,
        currentPage: state.currentPage,
        lastQuery: state.lastQuery,
        lastFilters: state.lastFilters,
        scrollY: state.scrollY,
      }),
    },
  ),
);
