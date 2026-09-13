import React, {
  useState,
  useRef,
  useEffect,
  useCallback,
  useMemo,
} from 'react';
import { useNavigate, useLocation } from '@tanstack/react-router';
import { AssistantRuntimeProvider } from '@assistant-ui/react';
import {
  Plus,
  FileText,
  Check,
  TrashSimple,
  SidebarSimple,
  Fire,
  X,
  SpinnerGap,
} from '@phosphor-icons/react';
import { buildChatFileContentUrl, ragApi } from '../../lib/api/chat';
import { paperApi } from '../../lib/api/papers';
import { Thread, type CitationClickPayload } from './assistant/Thread';
import {
  clearPersistedChatHistory,
  useBloomIndexRuntime,
  type Citation,
  type RagSource,
} from './assistant/chatRuntimeAdapter';
import { CitationPreviewDrawer } from './CitationPreviewDrawer';
import { useUploadStore } from '../../stores/uploadStore';
import { useChatStore, type UploadedFile } from '../../stores/chatStore';
import { useIndexedFiles, indexedFilesKey } from '../../hooks/useIndexedFiles';
import { useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import type { IndexedFileInfo, UploadJobStatus } from '../../types';

interface RagLocationState {
  importPaperPdf?: {
    identifier: string;
    title?: string;
  };
}

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

/** Custom styled checkbox matching the reference design. */
function CustomCheckbox({
  checked,
  onChange,
}: {
  checked: boolean;
  onChange: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onChange}
      className={`
        w-5 h-5 rounded-[5px] flex-shrink-0 flex items-center justify-center
        transition-all duration-150 border cursor-pointer
        ${
          checked
            ? 'bg-slate-900 border-slate-900 text-white hover:bg-black hover:border-black'
            : 'bg-background border-slate-300 hover:border-slate-500'
        }
      `}
    >
      {checked && <Check size={13} weight="bold" className="text-white" />}
    </button>
  );
}

function SimplePdfViewer({ pdfUrl }: { pdfUrl: string }) {
  return (
    <iframe
      key={pdfUrl}
      src={pdfUrl}
      title="PDF"
      className="h-full w-full rounded-xl border-0"
    />
  );
}

interface ChatThreadAreaProps {
  getSelectedFiles: () => string[];
  onCitationClick: (payload: CitationClickPayload) => void;
}

const ChatThreadArea: React.FC<ChatThreadAreaProps> = ({
  getSelectedFiles,
  onCitationClick,
}) => {
  const runtime = useBloomIndexRuntime(
    useMemo(
      () => ({ getSelectedFiles, enableSessionPersistence: true }),
      [getSelectedFiles],
    ),
  );

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <Thread onCitationClick={onCitationClick} />
    </AssistantRuntimeProvider>
  );
};

async function pollJobUntilDone(
  jobId: string,
  timeoutMs = 180000,
): Promise<UploadJobStatus> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const status = await ragApi.getUploadStatus(jobId);
    if (status.status === 'completed' || status.status === 'failed') {
      return status;
    }
    await new Promise((r) => setTimeout(r, 600));
  }
  throw new Error('Processing timed out');
}

// Chat history is persisted by the assistant-ui runtime under
// `bi_chat_history` (see ./assistant/chatRuntimeAdapter.ts). Page UI state
// (parserType / uploadedFiles / sidebarCollapsed) lives in the Zustand
// chatStore (key `bi_chat_state`), same sessionStorage backing.
const ChatPage: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const locationState = location.state as RagLocationState | undefined;
  const queryClient = useQueryClient();
  const [chatSessionKey, setChatSessionKey] = useState(0);
  // Upload status + isUploading live in the shared Zustand store so
  // ChatPage and the layout Sidebar always agree on whether an upload
  // is in flight (see frontend/src/stores/uploadStore.ts). The store
  // selectors use individual getters so re-renders only fire when
  // the slice the component reads actually changes.
  const uploadStatus = useUploadStore((s) => s.status);
  const setUploadStatus = useUploadStore((s) => s.setStatus);
  const isUploading = useUploadStore((s) => s.isUploading);
  const setIsUploading = useUploadStore((s) => s.setIsUploading);
  // ── Persisted chat page UI state (sessionStorage-backed, per-tab) ───────
  // Hydration on mount is automatic via the persist middleware; effects
  // that used to read/write sessionStorage by hand are gone.
  const parserType = useChatStore((s) => s.parserType);
  const setParserType = useChatStore((s) => s.setParserType);
  const uploadedFiles = useChatStore((s) => s.uploadedFiles);
  const setUploadedFiles = useChatStore((s) => s.setUploadedFiles);
  const sidebarCollapsed = useChatStore((s) => s.sidebarCollapsed);
  const setSidebarCollapsed = useChatStore((s) => s.setSidebarCollapsed);
  const resetUploadedFiles = useChatStore((s) => s.resetUploadedFiles);

  const fileInputRef = useRef<HTMLInputElement>(null);
  // ``triggerKey`` is bumped on every citation click so the markdown
  // preview can restart its flash animation even when the user clicks
  // the same [N] twice in a row (otherwise React would reconcile the
  // existing element and the CSS animation wouldn't replay).
  const [activeCitation, setActiveCitation] = useState<{
    source: RagSource;
    citation?: Citation;
    triggerKey: number;
  } | null>(null);
  const [activePdfFile, setActivePdfFile] = useState<UploadedFile | null>(null);
  const [activePdfUrl, setActivePdfUrl] = useState<string | null>(null);
  const importedPaperRef = useRef<string | null>(null);

  // Build the assistant-ui runtime getter. The selected-file getter is read on
  // every send so the user's checkbox state always reflects in the
  // outgoing /api/chat/query/json request.
  const uploadedFilesRef = useRef(uploadedFiles);
  // Mirror for the stable send-callback below. Assigned in an effect so the
  // ref is never written during render; callbacks always run post-commit.
  useEffect(() => {
    uploadedFilesRef.current = uploadedFiles;
  }, [uploadedFiles]);
  const getSelectedFiles = useCallback(
    () => uploadedFilesRef.current.filter((f) => f.selected).map((f) => f.name),
    [],
  );

  // (No manual interval cleanup needed — TanStack Query stops the
  // upload-status poll automatically when its query goes inactive,
  // and the App-level UploadStatusListener clears ``currentJobId``
  // on terminal status. The previous ``pollIntervalRef`` cleanup is
  // gone with the manual setInterval that needed it.)

  // Server state for the indexed-files list lives in TanStack Query
  // (see frontend/src/hooks/useIndexedFiles.ts). The query auto-fetches
  // on mount, dedupes across tabs/components, and is invalidated after
  // every upload completion. The selection-status merge below is the
  // same defensive logic we used before: if the backend reports an
  // empty list while we have files locally (e.g., during the eventual-
  // consistency window right after upload), we preserve the locals so
  // the UI doesn't flash empty.
  const { data: indexedFilesData } = useIndexedFiles();

  // Merge server payload into the local ``uploadedFiles`` whenever the
  // query data changes. ``setUploadedFiles`` is a stable React setter
  // so this effect only refires when the backend payload changes.
  useEffect(() => {
    if (!indexedFilesData) return;
    setUploadedFiles((prev) => {
      if (indexedFilesData.length === 0 && prev.length > 0) {
        // Backend returned no files but we have locals — keep locals.
        return prev;
      }

      const selectionMap = new Map(prev.map((f) => [f.name, f.selected]));
      const summaryMap = new Map(prev.map((f) => [f.name, f.summary]));

      return indexedFilesData.map((f) => ({
        name: f.name,
        fileType: f.file_type,
        chunkCount: f.chunk_count,
        // If we had a selection status for this file before, preserve it; otherwise default to true
        selected: selectionMap.has(f.name) ? !!selectionMap.get(f.name) : true,
        parserType: f.parser_type as 'pymupdf' | 'docling',
        authors: f.authors || '',
        doi: f.doi || '',
        journal: f.journal || '',
        summary: f.summary || summaryMap.get(f.name) || '',
      }));
    });
  }, [indexedFilesData]);

  const closePdfViewer = useCallback(() => {
    setActivePdfFile(null);
    setActivePdfUrl(null);
  }, []);

  const applyUploadResult = useCallback(
    (
      result: { files: string[]; summaries?: Record<string, string> },
      activeParserType: 'pymupdf' | 'docling',
    ) => {
      setUploadedFiles((prev) => {
        const existingNames = new Set(prev.map((f) => f.name));
        const newFiles: UploadedFile[] = result.files
          .filter((name) => !existingNames.has(name))
          .map((name) => {
            const ext =
              name.lastIndexOf('.') !== -1
                ? name.slice(name.lastIndexOf('.'))
                : '.pdf';
            return {
              name,
              fileType: ext,
              chunkCount: 0,
              selected: true,
              parserType: activeParserType,
              summary: result.summaries?.[name] || '',
            };
          });
        return [...prev, ...newFiles];
      });
    },
    [],
  );

  // The upload-status polling that used to live here as
  // ``startUploadPolling`` (with its own setInterval, pollIntervalRef,
  // and inline status handling) has moved to a single App-level
  // ``UploadStatusListener`` driven by TanStack Query (see
  // frontend/src/components/UploadStatusListener.tsx). Trigger a poll
  // by writing the new ``job_id`` into the upload store via
  // ``setCurrentJobId(jobId)`` after the multipart POST returns. The
  // listener handles status text, completion side-effects, and cache
  // invalidation centrally — no per-component refs to manage.
  const openPdfViewer = useCallback((file: UploadedFile) => {
    // Automatically close sources/citation preview when opening PDF viewer
    setActiveCitation(null);
    setActivePdfFile(file);
    setActivePdfUrl(buildChatFileContentUrl(file.name));
  }, []);

  // (Per-slice sessionStorage writes that used to live here are gone —
  // the chatStore's persist middleware handles parserType /
  // uploadedFiles / sidebarCollapsed automatically. Chat messages are
  // still owned by the assistant-ui runtime via its ThreadHistoryAdapter,
  // see ./assistant/chatRuntimeAdapter.ts — a separate sessionStorage key.)

  // The on-mount fetch that used to live here is gone — ``useIndexedFiles``
  // auto-fetches on mount via TanStack Query, so a second refetch here
  // would just duplicate the request. Polling/refresh after upload is
  // handled by ``invalidateIndexedFiles()`` in the upload completion
  // path; manual refetches still go through ``loadIndexedFiles()``.

  // If the previewed file vanished (deleted while previewing), close the
  // viewer in the same commit — no flash of stale content, no extra pass.
  if (
    activePdfFile &&
    !uploadedFiles.some((file) => file.name === activePdfFile.name)
  ) {
    closePdfViewer();
  }

  useEffect(() => {
    const pendingImport = locationState?.importPaperPdf;
    if (!pendingImport?.identifier) {
      return;
    }
    if (importedPaperRef.current === pendingImport.identifier) {
      return;
    }

    importedPaperRef.current = pendingImport.identifier;

    const importPaperPdf = async () => {
      setIsUploading(true);
      setUploadStatus('Processing 1/1');

      try {
        const { blob, filename } = await paperApi.fetchPdf(
          pendingImport.identifier,
        );
        const safeBase =
          (pendingImport.title || filename || 'paper')
            .replace(/<[^>]+>/g, '')
            .replace(/[^a-zA-Z0-9._-]+/g, '_')
            .replace(/^_+|_+$/g, '')
            .slice(0, 120) || 'paper';
        const finalName = filename.toLowerCase().endsWith('.pdf')
          ? filename
          : `${safeBase}.pdf`;
        const file = new File([blob], finalName, { type: 'application/pdf' });
        const result = await ragApi.uploadFiles([file], parserType);
        if (result.status === 'processing' && result.job_id) {
          const finalJob = await pollJobUntilDone(result.job_id);
          if (finalJob.status === 'failed') {
            throw new Error(finalJob.error || 'Paper PDF processing failed');
          }
          // Appear instantly in sidebar ONLY AFTER processing completes
          applyUploadResult(
            { files: finalJob.files || [finalName] },
            parserType,
          );
          await queryClient.refetchQueries({ queryKey: indexedFilesKey });
        } else {
          applyUploadResult(result, parserType);
          await queryClient.refetchQueries({ queryKey: indexedFilesKey });
        }
      } catch (error) {
        console.error('Paper PDF import failed:', error);
        toast.error(
          'Paper PDF import failed. Please try downloading it manually.',
        );
      } finally {
        setIsUploading(false);
        setUploadStatus('');
        navigate({ to: '/chat', replace: true, state: {} });
      }
    };

    importPaperPdf();
  }, [
    applyUploadResult,
    locationState,
    navigate,
    parserType,
    queryClient,
    setIsUploading,
    setUploadStatus,
  ]);

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;

    const fileArr = Array.from(files);
    const total = fileArr.length;
    setIsUploading(true);

    try {
      for (let i = 0; i < total; i++) {
        const file = fileArr[i];
        setUploadStatus(`Processing ${i + 1}/${total}`);

        const result = await ragApi.uploadFiles([file], parserType);
        if (result.status === 'processing' && result.job_id) {
          const finalJob = await pollJobUntilDone(result.job_id);
          if (finalJob.status === 'failed') {
            throw new Error(finalJob.error || 'Processing failed');
          }
          // Only add to sidebar AFTER pymupdf or docling processing completes!
          applyUploadResult(
            { files: finalJob.files || [file.name] },
            parserType,
          );
          await queryClient.refetchQueries({ queryKey: indexedFilesKey });
        } else {
          applyUploadResult(result, parserType);
          await queryClient.refetchQueries({ queryKey: indexedFilesKey });
        }
      }
    } catch (error) {
      console.error('Upload failed:', error);
      toast.error('Upload failed. Please try again.');
    } finally {
      setIsUploading(false);
      setUploadStatus('');
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleUploadClick = () => {
    fileInputRef.current?.click();
  };

  const handleDeleteFile = async (filename: string, e: React.MouseEvent) => {
    e.stopPropagation(); // Don't toggle checkbox
    if (activePdfFile?.name === filename) {
      closePdfViewer();
    }
    setUploadedFiles((prev) => prev.filter((f) => f.name !== filename));
    queryClient.setQueryData<IndexedFileInfo[]>(indexedFilesKey, (prev) =>
      prev ? prev.filter((f) => f.name !== filename) : [],
    );
    try {
      await ragApi.deleteFile(filename);
    } catch (error) {
      console.error('Delete failed:', error);
      queryClient.invalidateQueries({ queryKey: indexedFilesKey });
    }
  };

  const toggleFile = (name: string) => {
    setUploadedFiles((prev) =>
      prev.map((f) => (f.name === name ? { ...f, selected: !f.selected } : f)),
    );
  };

  const handleResetAll = async () => {
    const confirmMessage =
      'Delete all chats and source files? This cannot be undone.';
    if (!window.confirm(confirmMessage)) {
      return;
    }

    // 1. Immediately close any open preview & active citations
    closePdfViewer();
    setActiveCitation(null);

    // 2. Immediately clear sources in Zustand store and TanStack Query cache
    resetUploadedFiles();
    queryClient.setQueryData(indexedFilesKey, []);

    // 3. Immediately clear persisted chat messages from sessionStorage
    clearPersistedChatHistory();

    // 4. Immediately remount the thread with fresh empty state (0 delay, no page reload)
    setChatSessionKey((prev) => prev + 1);

    // 5. Fire backend reset in background
    try {
      await ragApi.resetChat();
      queryClient.invalidateQueries({ queryKey: indexedFilesKey });
    } catch (error) {
      console.error('Reset failed on backend:', error);
    }
  };

  /** Strip file extension for cleaner display. */
  const displayName = (name: string) => {
    const dotIdx = name.lastIndexOf('.');
    return dotIdx > 0 ? name.slice(0, dotIdx) : name;
  };

  const handleCitationClick = useCallback(
    (payload: CitationClickPayload) => {
      // Ignore clicks where the chunk_id no longer resolves
      // to a known source (rare; can happen if a stored
      // assistant message references a chunk we've since
      // wiped via "Reset all").
      if (!payload.source) return;
      // Automatically close PDF viewer when opening citation preview
      closePdfViewer();
      setActiveCitation({
        source: payload.source,
        citation: payload.citation,
        triggerKey: Date.now(),
      });
    },
    [closePdfViewer],
  );

  return (
    <div
      className="chat-section h-full flex px-0"
      style={{ fontFamily: 'var(--font-google-sans)' }}
    >
      {/* ─── Sources Sidebar ─── */}
      <aside
        className={`sidebar-transition border-r border-surface-c bg-background flex flex-col shrink-0 relative h-full ${
          sidebarCollapsed ? 'sidebar-collapsed' : 'w-72'
        }`}
      >
        <input
          type="file"
          ref={fileInputRef}
          onChange={handleFileUpload}
          multiple
          accept=".pdf"
          className="hidden"
        />

        {/* Unified Top Header Bar */}
        <div className="px-3.5 h-14 border-b border-surface-c flex items-center justify-between shrink-0">
          <span
            className={`!text-[17px] !font-bold text-slate-900 tracking-tight whitespace-nowrap transition-opacity duration-150 ${sidebarCollapsed ? 'hidden' : 'block'}`}
          >
            Sources
          </span>
          <button
            onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
            className={`p-1.5 text-slate-600 hover:text-slate-900 rounded-md hover:bg-surface-c active:scale-90 transition-all duration-100 outline-none cursor-pointer ${sidebarCollapsed ? 'mx-auto' : ''}`}
            aria-label={
              sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'
            }
          >
            <SidebarSimple size={20} />
          </button>
        </div>

        {/* Mini View (Icons only) */}
        <div className="sidebar-mini-view flex-1 min-h-0 flex-col items-center pt-3.5 gap-2 w-full overflow-y-auto chat-scrollbar pb-4">
          <button
            onClick={handleUploadClick}
            disabled={isUploading}
            style={{
              backgroundColor: '#ffecf6',
              color: '#d63384',
              borderColor: '#fbcfe8',
              boxShadow: 'none',
            }}
            className="w-10 h-10 rounded-full border flex items-center justify-center transition-all hover:opacity-90 text-[#d63384] shadow-none outline-none disabled:cursor-wait cursor-pointer active:scale-95 shrink-0"
            title={
              isUploading ? uploadStatus || 'Processing 1/1' : 'Add Sources'
            }
          >
            {isUploading ? (
              <SpinnerGap
                size={18}
                weight="bold"
                className="animate-spin text-[#d63384] shrink-0"
              />
            ) : (
              <Plus
                size={18}
                weight="bold"
                className="text-[#d63384] shrink-0"
              />
            )}
          </button>

          {uploadedFiles.length > 0 && (
            <div className="w-5 h-[1.5px] bg-slate-200/80 rounded-full mx-auto my-1 shrink-0" />
          )}

          <div className="flex-1 overflow-y-auto px-2 py-1 space-y-3 flex flex-col items-center chat-scrollbar w-full">
            {uploadedFiles.map((file) => {
              const isActive = activePdfFile?.name === file.name;
              return (
                <button
                  key={file.name}
                  type="button"
                  title={displayName(file.name)}
                  onClick={() => openPdfViewer(file)}
                  className={`w-10 h-10 rounded-xl flex items-center justify-center transition-all cursor-pointer outline-none border-0 shrink-0 ${
                    isActive
                      ? 'bg-slate-200/90 text-slate-900 shadow-sm opacity-100'
                      : file.selected
                        ? 'hover:bg-surface-c text-slate-700 opacity-100'
                        : 'hover:bg-surface-c text-slate-700 opacity-40 hover:opacity-80'
                  }`}
                >
                  <PdfIcon size={24} className="shrink-0" />
                </button>
              );
            })}
          </div>

          <div className="p-4 mt-auto flex items-center justify-center shrink-0">
            <button
              type="button"
              onClick={handleResetAll}
              style={{ boxShadow: 'none' }}
              className="w-10 h-10 rounded-full border border-red-200 hover:bg-red-50 hover:border-red-300 text-red-600 flex items-center justify-center transition-all shadow-none outline-none cursor-pointer active:scale-95"
              title="Delete Chats"
            >
              <Fire
                size={18}
                weight="regular"
                className="text-red-600 shrink-0"
              />
            </button>
          </div>
        </div>

        {/* Main Sidebar Content */}
        <div
          className={`sidebar-content flex-1 min-h-0 flex flex-col overflow-hidden w-72 min-w-[18rem] shrink-0 transition-opacity duration-150 ${sidebarCollapsed ? 'opacity-0 pointer-events-none' : 'opacity-100'}`}
        >
          {/* Upload controls */}
          <div className="px-4 py-3 space-y-2 shrink-0">
            {/* Parser type toggle matching Analyse switcher pill */}
            <div
              className="flex items-center rounded-full p-1 border-0"
              style={{ background: 'var(--surface-c)' }}
            >
              <button
                type="button"
                onClick={() => setParserType('pymupdf')}
                className="flex-1 h-9 rounded-full text-[14px] transition-all flex items-center justify-center outline-none border-0 cursor-pointer"
                style={{
                  fontFamily: 'var(--font-google-sans)',
                  boxShadow: 'none',
                  background:
                    parserType === 'pymupdf' ? '#FFFFFF' : 'transparent',
                  color: parserType === 'pymupdf' ? '#000000' : '#666666',
                  fontWeight: parserType === 'pymupdf' ? 600 : 500,
                }}
              >
                Fast
              </button>
              <button
                type="button"
                onClick={() => setParserType('docling')}
                className="flex-1 h-9 rounded-full text-[14px] transition-all flex items-center justify-center outline-none border-0 cursor-pointer"
                style={{
                  fontFamily: 'var(--font-google-sans)',
                  boxShadow: 'none',
                  background:
                    parserType === 'docling' ? '#FFFFFF' : 'transparent',
                  color: parserType === 'docling' ? '#000000' : '#666666',
                  fontWeight: parserType === 'docling' ? 600 : 500,
                }}
              >
                Detailed
              </button>
            </div>

            <button
              onClick={handleUploadClick}
              disabled={isUploading}
              style={{
                backgroundColor: '#ffecf6',
                color: '#d63384',
                borderColor: '#fbcfe8',
                fontFamily: 'var(--font-google-sans)',
                boxShadow: 'none',
              }}
              className="w-full py-2.5 px-4 rounded-full border border-[#fbcfe8] flex items-center justify-center gap-2 text-[14.5px] font-bold transition-all hover:opacity-95 active:scale-[0.99] text-[#d63384] shadow-none outline-none disabled:cursor-wait cursor-pointer"
            >
              {isUploading ? (
                <SpinnerGap
                  size={18}
                  weight="bold"
                  className="animate-spin text-[#d63384] shrink-0"
                />
              ) : (
                <Plus
                  size={18}
                  weight="bold"
                  className="text-[#d63384] shrink-0"
                />
              )}
              <span className="font-bold text-[#d63384] whitespace-nowrap">
                {isUploading ? uploadStatus || 'Processing 1/1' : 'Add Sources'}
              </span>
            </button>
          </div>

          {/* Subtle divider before file stack */}
          <div className="mx-4 mb-2 h-[1px] bg-slate-200/60 shrink-0" />

          {/* File List */}
          <div className="flex-1 overflow-y-auto px-3 pb-4 space-y-1 flex flex-col chat-scrollbar">
            {uploadedFiles.length > 0 ? (
              <div>
                {uploadedFiles.map((file) => (
                  <div
                    key={file.name}
                    onClick={() => openPdfViewer(file)}
                    className={`w-full flex items-center space-x-3 p-3 rounded-2xl transition-all group cursor-pointer border-0 shadow-none outline-none ${
                      activePdfFile?.name === file.name
                        ? 'bg-[#f4f4f4] opacity-100'
                        : file.selected
                          ? 'bg-transparent hover:bg-[#fafafa] opacity-100'
                          : 'bg-transparent hover:bg-[#fafafa] opacity-70 hover:opacity-100'
                    }`}
                  >
                    <PdfIcon size={24} className="shrink-0" />
                    <div className="flex-1 min-w-0 text-left">
                      <p
                        className={`text-[14px] truncate leading-tight ${activePdfFile?.name === file.name ? 'font-bold text-slate-900' : 'font-medium text-slate-700'}`}
                      >
                        {displayName(file.name)}
                      </p>
                      {(file.authors || file.journal) && (
                        <p className="text-[11px] text-on-surface-muted truncate leading-tight mt-0.5">
                          {file.authors && <span>{file.authors}</span>}
                          {file.authors && file.journal && <span> · </span>}
                          {file.journal && (
                            <span className="italic">{file.journal}</span>
                          )}
                        </p>
                      )}
                      {file.summary && (
                        <p
                          className="text-[11px] text-on-surface-muted line-clamp-2 leading-snug mt-0.5"
                          title={file.summary}
                        >
                          {file.summary}
                        </p>
                      )}
                    </div>
                    <button
                      onClick={(e) => handleDeleteFile(file.name, e)}
                      className="opacity-0 group-hover:opacity-100 text-slate-400 hover:text-red-500 transition-all p-1 rounded-md hover:bg-red-50 flex-shrink-0 cursor-pointer"
                      title={`Remove ${file.name}`}
                    >
                      <TrashSimple size={16} weight="regular" />
                    </button>
                    <div onClick={(e) => e.stopPropagation()}>
                      <CustomCheckbox
                        checked={file.selected}
                        onChange={() => toggleFile(file.name)}
                      />
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="flex-1 flex flex-col items-center justify-center text-center px-4 py-8">
                <FileText
                  size={40}
                  className="text-on-surface-muted/30 mb-3 shrink-0"
                />
                <p className="text-[14px] text-on-surface-muted leading-relaxed font-normal whitespace-nowrap">
                  No sources added yet.
                </p>
              </div>
            )}
          </div>

          {/* Sidebar Footer: Delete Chats */}
          <div className="p-4 bg-background mt-auto shrink-0">
            <button
              onClick={handleResetAll}
              style={{
                fontFamily: 'var(--font-google-sans)',
                boxShadow: 'none',
              }}
              className="w-full flex items-center justify-center space-x-2 py-2.5 px-4 bg-background border border-red-200 hover:bg-red-50 hover:border-red-300 text-red-600 rounded-full transition-all font-semibold text-[14.5px] shadow-none outline-none cursor-pointer"
            >
              <Fire size={18} weight="regular" className="shrink-0" />
              <span className="whitespace-nowrap">Delete Chats</span>
            </button>
          </div>
        </div>
      </aside>

      {/* ─── Chat Area (assistant-ui Thread) ─── */}
      <div className="flex-1 min-w-0 flex flex-col relative">
        <ChatThreadArea
          key={chatSessionKey}
          getSelectedFiles={getSelectedFiles}
          onCitationClick={handleCitationClick}
        />
      </div>

      {activePdfFile && (
        <aside className="w-[min(32rem,42vw)] min-w-[22rem] border-l border-surface-c bg-background flex flex-col">
          <div className="flex items-center justify-between px-4 py-3 border-b border-surface-c">
            <div className="min-w-0 flex items-center gap-2.5">
              <PdfIcon size={20} className="shrink-0" />
              <h3
                className="text-sm font-semibold text-on-surface truncate"
                title={activePdfFile.name}
              >
                {displayName(activePdfFile.name)}
              </h3>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={closePdfViewer}
                className="p-1 text-black hover:text-black hover:opacity-75 transition-opacity outline-none border-0 bg-transparent flex items-center justify-center cursor-pointer"
                title="Close"
              >
                <X size={18} weight="bold" className="text-black" />
              </button>
            </div>
          </div>

          <div className="flex-1 overflow-auto bg-surface-c p-2 chat-scrollbar">
            {activePdfUrl ? (
              <SimplePdfViewer pdfUrl={activePdfUrl} />
            ) : (
              <div className="h-full min-h-[16rem] flex items-center justify-center text-sm text-on-surface-muted">
                Select a PDF to preview it here.
              </div>
            )}
          </div>
        </aside>
      )}

      {/* ─── Citation markdown preview panel ─── */}
      {activeCitation && (
        <CitationPreviewDrawer
          source={activeCitation.source}
          citation={activeCitation.citation}
          triggerKey={activeCitation.triggerKey}
          onClose={() => setActiveCitation(null)}
        />
      )}
    </div>
  );
};

export default ChatPage;
