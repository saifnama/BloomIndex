import { api } from './client';
import type {
  QueryResponse,
  UploadResponse,
  UploadJobStatus,
  IndexedFileInfo,
} from '../../types';

export const ragApi = {
  /**
   * Uploads PDF files for text extraction and vector indexing.
   */
  async uploadFiles(
    files: File[],
    parserType: 'pymupdf' | 'docling' = 'pymupdf',
  ): Promise<UploadResponse> {
    const formData = new FormData();
    files.forEach((file) => {
      formData.append('files', file);
    });
    formData.append('parser_type', parserType);

    const response = await api.post<UploadResponse>(
      '/api/chat/upload/json',
      formData,
    );
    return response.data;
  },

  /**
   * Uploads documents in sequential batches to prevent proxy timeouts
   * and excessive browser/server memory consumption on large queues.
   */
  async uploadFilesChunked(
    files: File[],
    parserType: 'pymupdf' | 'docling' = 'pymupdf',
    chunkSize = 20,
    onBatch?: (
      batchIndex: number,
      totalBatches: number,
      batchResult: UploadResponse,
    ) => void,
  ): Promise<UploadResponse> {
    if (files.length === 0) {
      throw new Error('uploadFilesChunked: no files');
    }
    const batches: File[][] = [];
    for (let i = 0; i < files.length; i += chunkSize) {
      batches.push(files.slice(i, i + chunkSize));
    }
    let last: UploadResponse | undefined;
    for (let i = 0; i < batches.length; i += 1) {
      const result = await ragApi.uploadFiles(batches[i], parserType);
      last = result;
      if (onBatch) {
        onBatch(i, batches.length, result);
      }
    }
    if (!last) {
      throw new Error('uploadFilesChunked: no batches succeeded');
    }
    return last;
  },

  /**
   * Polls background extraction and indexing progress for a job.
   */
  async getUploadStatus(jobId: string): Promise<UploadJobStatus> {
    const response = await api.get<UploadJobStatus>(
      `/api/chat/upload/status/${encodeURIComponent(jobId)}`,
    );
    return response.data;
  },

  /**
   * Retrieves metadata for all documents currently indexed in ChromaDB.
   */
  async listFiles(): Promise<IndexedFileInfo[]> {
    const response = await api.get('/api/chat/files/json');
    return response.data;
  },

  /**
   * Submits a question to the RAG pipeline with optional source filters
   * and conversational history.
   */
  async query(
    query: string,
    selectedFiles?: string[],
    chatHistory?: { role: string; content: string }[],
  ): Promise<QueryResponse> {
    const response = await api.post<QueryResponse>('/api/chat/query/json', {
      query,
      selected_files: selectedFiles,
      chat_history: chatHistory,
    });
    return response.data;
  },

  /**
   * Removes document chunks from ChromaDB and deletes raw storage.
   */
  async deleteFile(
    filename: string,
  ): Promise<{ status: string; message: string }> {
    const response = await api.delete(
      `/api/chat/files/${encodeURIComponent(filename)}`,
    );
    return response.data;
  },

  /**
   * Flushes chat conversations and associated vector collections.
   */
  async resetChat(): Promise<{ status: string; message: string }> {
    const response = await api.post('/api/chat/reset');
    return response.data;
  },

  /**
   * Cleans up session storage and vector records on browser termination.
   */
  async cleanupUserData(): Promise<{ status: string; message: string }> {
    const response = await api.post('/api/chat/cleanup');
    return response.data;
  },
};

export const buildChatFileContentUrl = (filename: string): string => {
  return `/api/chat/files/${encodeURIComponent(filename)}/content`;
};
