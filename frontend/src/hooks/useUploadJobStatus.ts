/**
 * Polling hook tracking async background upload and vector indexing jobs.
 *
 * Deduplicates polling across observing components via a shared query
 * key and automatically terminates requests upon completion or failure.
 */
import { useQuery } from '@tanstack/react-query';
import { ragApi } from '../lib/api/chat';
import type { UploadJobStatus } from '../types';

const POLL_INTERVAL_MS = 1000;

export function useUploadJobStatus(jobId: string | null) {
  return useQuery<UploadJobStatus>({
    queryKey: ['upload-status', jobId],
    queryFn: () => ragApi.getUploadStatus(jobId as string),
    enabled: !!jobId,
    // Polls while processing; halts when reaching a terminal state.
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === 'completed' || status === 'failed') return false;
      return POLL_INTERVAL_MS;
    },
    // Avoids redundant re-fetching upon window or tab focus transitions.
    refetchOnWindowFocus: false,
    // Cache freshness window matches the active polling interval.
    staleTime: POLL_INTERVAL_MS,
  });
}
