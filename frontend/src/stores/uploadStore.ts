/**
 * Shared document upload state coordinating background job polling.
 *
 * Synchronizes in-flight multipart uploads, active status text, and
 * job IDs across navigation bars, sidebars, and chat pages.
 */
import { create } from 'zustand';

interface UploadState {
  /**
   * Active flag during upload transit or background vectorization.
   */
  isUploading: boolean;
  /**
   * Human-readable progress description, empty when idle.
   */
  status: string;
  /**
   * Backend indexing job identifier currently being polled.
   */
  currentJobId: string | null;

  setIsUploading: (next: boolean) => void;
  setStatus: (next: string) => void;
  setCurrentJobId: (jobId: string | null) => void;
  /**
   * Resets upload status, active job tracking, and progress text.
   */
  reset: () => void;
}

export const useUploadStore = create<UploadState>((set) => ({
  isUploading: false,
  status: '',
  currentJobId: null,
  setIsUploading: (next) => set({ isUploading: next }),
  setStatus: (next) => set({ status: next }),
  setCurrentJobId: (jobId) => set({ currentJobId: jobId }),
  reset: () => set({ isUploading: false, status: '', currentJobId: null }),
}));
