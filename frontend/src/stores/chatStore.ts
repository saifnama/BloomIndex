/**
 * Chat page UI state persisted across route navigation in sessionStorage.
 *
 * Maintains parser preferences, user document selections, and sidebar
 * layout. Chat message history is delegated to assistant-ui adapters,
 * and transient PDF viewer states remain component-scoped.
 */

import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

export interface UploadedFile {
  name: string;
  fileType: string;
  chunkCount: number;
  selected: boolean;
  parserType: 'pymupdf' | 'docling';
  authors?: string;
  doi?: string;
  journal?: string;
  summary?: string;
}

type Updater<T> = T | ((prev: T) => T);

interface ChatState {
  parserType: 'pymupdf' | 'docling';
  uploadedFiles: UploadedFile[];
  sidebarCollapsed: boolean;

  setParserType: (next: 'pymupdf' | 'docling') => void;
  setUploadedFiles: (next: Updater<UploadedFile[]>) => void;
  setSidebarCollapsed: (next: Updater<boolean>) => void;
  /**
   * Clears uploaded files on session reset while preserving preferences.
   */
  resetUploadedFiles: () => void;
}

const INITIAL: Pick<
  ChatState,
  'parserType' | 'uploadedFiles' | 'sidebarCollapsed'
> = {
  parserType: 'pymupdf',
  uploadedFiles: [],
  sidebarCollapsed: false,
};

function applyUpdater<T>(next: Updater<T>, prev: T): T {
  return typeof next === 'function' ? (next as (p: T) => T)(prev) : next;
}

export const useChatStore = create<ChatState>()(
  persist(
    (set) => ({
      ...INITIAL,

      setParserType: (next) => set({ parserType: next }),

      setUploadedFiles: (next) =>
        set((state) => ({
          uploadedFiles: applyUpdater(next, state.uploadedFiles),
        })),

      setSidebarCollapsed: (next) =>
        set((state) => ({
          sidebarCollapsed: applyUpdater(next, state.sidebarCollapsed),
        })),

      resetUploadedFiles: () => set({ uploadedFiles: [] }),
    }),
    {
      name: 'bi_chat_state',
      storage: createJSONStorage(() => sessionStorage),
      partialize: (state) => ({
        parserType: state.parserType,
        uploadedFiles: state.uploadedFiles,
        sidebarCollapsed: state.sidebarCollapsed,
      }),
    },
  ),
);
