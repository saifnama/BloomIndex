/**
 * Server query hook managing indexed document metadata in ChromaDB.
 *
 * Provides request deduplication and background cache synchronization
 * across multiple UI components querying the document library.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ragApi } from '../lib/api/chat';
import type { IndexedFileInfo } from '../types';

/**
 * Stable query key used for cache reads and invalidation mutations.
 */
export const indexedFilesKey = ['rag', 'indexed-files'] as const;

export function useIndexedFiles() {
  return useQuery<IndexedFileInfo[]>({
    queryKey: indexedFilesKey,
    queryFn: () => ragApi.listFiles(),
  });
}

/**
 * Invalidation trigger forcing fresh retrieval of indexed documents.
 */
export function useInvalidateIndexedFiles() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: indexedFilesKey });
}
