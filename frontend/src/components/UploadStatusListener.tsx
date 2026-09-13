/**
 * Headless layout listener monitoring background upload job progression.
 *
 * Observes active job IDs from uploadStore, updates progress text,
 * synchronizes completed documents into chatStore, and invalidates
 * indexed document queries upon completion.
 */
import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { useUploadStore } from '../stores/uploadStore';
import { useUploadJobStatus } from '../hooks/useUploadJobStatus';
import { indexedFilesKey } from '../hooks/useIndexedFiles';

import { useChatStore, type UploadedFile } from '../stores/chatStore';

export function UploadStatusListener() {
  const currentJobId = useUploadStore((s) => s.currentJobId);
  const setCurrentJobId = useUploadStore((s) => s.setCurrentJobId);
  const setStatus = useUploadStore((s) => s.setStatus);
  const setIsUploading = useUploadStore((s) => s.setIsUploading);
  const queryClient = useQueryClient();

  const { data: jobStatus } = useUploadJobStatus(currentJobId);

  useEffect(() => {
    if (!jobStatus || !currentJobId) return;

    if (jobStatus.status === 'completed') {
      const count = jobStatus.files.length;
      setStatus(`Indexed ${count} file${count === 1 ? '' : 's'}.`);
      setIsUploading(false);

      // Optimistically seeds completed document entries into chatStore.
      const chatStore = useChatStore.getState();
      chatStore.setUploadedFiles((prev) => {
        const existing = new Set(prev.map((f) => f.name));
        const newEntries: UploadedFile[] = (jobStatus.files || [])
          .filter((name) => !existing.has(name))
          .map((name) => ({
            name,
            fileType: '.pdf',
            chunkCount: 0,
            selected: true,
            parserType:
              (jobStatus.parser_type as 'pymupdf' | 'docling') || 'pymupdf',
            summary: '',
          }));
        return [...prev, ...newEntries];
      });

      // Refetches indexed files so chunk counts and metadata populate.
      void queryClient.refetchQueries({ queryKey: indexedFilesKey });

      // Clears the active job to terminate polling.
      setCurrentJobId(null);
    } else if (jobStatus.status === 'failed') {
      const reason = jobStatus.error || 'Unknown error';
      setStatus(`Upload failed: ${reason}`);
      setIsUploading(false);
      toast.error('Upload failed', { description: reason });
      setCurrentJobId(null);
    } else if (jobStatus.status === 'processing') {
      // Truncates document list preview to maintain readable status pill.
      const list = jobStatus.files.slice(0, 3).join(', ');
      const more =
        jobStatus.files.length > 3
          ? ` (+${jobStatus.files.length - 3} more)`
          : '';
      setStatus(`Processing ${list}${more}…`);
    }
  }, [
    jobStatus,
    currentJobId,
    queryClient,
    setCurrentJobId,
    setStatus,
    setIsUploading,
  ]);

  return null;
}
