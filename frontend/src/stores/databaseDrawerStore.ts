/**
 * Cross-page transient signal for requesting database drawer inspection.
 *
 * Allows external search bars to request the drawer to open with a
 * pre-populated filter query without coupling local drawer state.
 */

import { create } from 'zustand';

interface DrawerState {
  /**
   * Filter query requested by external search components, consumed
   * and cleared on drawer mount.
   */
  pendingOpenQuery: string | null;
  requestOpenWithQuery: (q: string) => void;
  clearPendingOpenQuery: () => void;
}

export const useDatabaseDrawerStore = create<DrawerState>((set) => ({
  pendingOpenQuery: null,
  requestOpenWithQuery: (q) => set({ pendingOpenQuery: q }),
  clearPendingOpenQuery: () => set({ pendingOpenQuery: null }),
}));
