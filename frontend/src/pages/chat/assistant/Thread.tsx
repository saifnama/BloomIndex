/**
 * assistant-ui chat thread for the BloomIndex RAG page — shadcn/ui build.
 *
 * Citation rendering pipeline:
 *   - Backend parses the LLM's inline ``[cN]`` self-report to build the
 *     ``References`` block (whole chunks, clickable), then strips all
 *     inline markers from the visible text.
 *   - ``MarkdownText`` runs a module-level ``stripInlineMarkers``
 *     preprocess as a safety net for any leaked ``[cN]``/``[N]`` — it
 *     has a stable identity on purpose: recreating it per render
 *     restarts the ``smooth`` streaming animation every token and can
 *     leave the visible text frozen mid-answer while the message itself
 *     is complete. References links (long labels) pass through to the
 *     custom markdown ``a`` component (CitationLink), which renders
 *     them as clickable pink text opening the preview panel in ChatPage.
 */

import {
  type FC,
  type ReactNode,
  createContext,
  useCallback,
  useContext,
  useState,
  useEffect,
} from 'react';
import {
  ActionBarPrimitive,
  BranchPickerPrimitive,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useAuiState,
} from '@assistant-ui/react';
import {
  MarkdownTextPrimitive,
  unstable_memoizeMarkdownComponents as memoizeMarkdownComponents,
} from '@assistant-ui/react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeRaw from 'rehype-raw';
import {
  ArrowUp,
  ArrowDown,
  Copy,
  Check,
  ArrowClockwise,
  PencilSimple,
  CaretLeft,
  CaretRight,
  FilePdf,
} from '@phosphor-icons/react';
import { Button } from '@/components/ui/button';
import { TooltipIconButton } from '@/components/assistant-ui/tooltip-icon-button';
import { Textarea } from '@/components/ui/textarea';
import type {
  Citation,
  RagMessageCustomData,
  RagSource,
} from './chatRuntimeAdapter';
import { exportThreadAsPdf, type ThreadTurn } from '@/utils/exportChatPdf';

const PINK_ACCENT = '#ff6dba';
const PINK_USER_BG = '#ffecf6';

export interface CitationClickPayload {
  chunkId: string;
  source?: RagSource;
  citation?: Citation;
}

interface ThreadProps {
  onCitationClick?: (payload: CitationClickPayload) => void;
  emptyContent?: ReactNode;
}

const CitationClickContext = createContext<
  ((chunkId: string) => void) | undefined
>(undefined);

const ThreadCitationClickContext = createContext<
  ((payload: CitationClickPayload) => void) | undefined
>(undefined);

export const Thread: FC<ThreadProps> = ({ onCitationClick, emptyContent }) => {
  return (
    <ThreadCitationClickContext.Provider value={onCitationClick}>
      <ThreadPrimitive.Root
        className="flex h-full flex-col bg-card"
        style={{
          fontFamily: 'var(--font-google-sans)',
          ['--thread-max-width' as string]: '54rem',
          ['--turn-gap-prompt-to-answer' as string]: '48px',
          ['--turn-gap-answer-to-prompt' as string]: '80px',
        }}
      >
        <ThreadPrimitive.Viewport className="relative flex-1 overflow-y-auto px-4 pt-8 pb-12 flex flex-col chat-scrollbar">
          <ThreadPrimitive.Empty>
            <div className="flex h-full items-center justify-center text-muted-foreground">
              {emptyContent ?? <DefaultEmpty />}
            </div>
          </ThreadPrimitive.Empty>

          <ThreadPrimitive.Messages components={threadComponents} />

          <ScrollToBottomButton />
        </ThreadPrimitive.Viewport>

        <Composer />
      </ThreadPrimitive.Root>
    </ThreadCitationClickContext.Provider>
  );
};

/**
 * Cumulative "export up to here" — clicking the button on the Nth
 * assistant message exports Q+A pairs 1 through N as a single PDF.
 * The button on answer #1 exports just pair 1; on answer #3, pairs 1+2+3.
 * Universal — uses the current message's position in the thread, no
 * hardcoded indices.
 */
const ExportAnswerPdfButton: FC = () => {
  const messages = useAuiState((s) => s.thread.messages);
  const messageId = useAuiState((s) => s.message.id);

  const handleExport = () => {
    const idx = messages.findIndex((m) => m.id === messageId);
    if (idx < 0) return;
    // Take every user / assistant turn from the start of the thread
    // through (and including) the assistant message this button sits on.
    const turns: ThreadTurn[] = messages
      .slice(0, idx + 1)
      .filter((m) => m.role === 'user' || m.role === 'assistant')
      .map((m) => ({
        role: m.role === 'user' ? 'user' : 'assistant',
        text: readMessageContent(m.content),
      }));
    exportThreadAsPdf({ turns });
  };

  return (
    <TooltipIconButton
      type="button"
      variant="ghost"
      size="icon-sm"
      onClick={handleExport}
      tooltip="Export to PDF"
      className="text-black hover:text-black hover:bg-slate-100 rounded-lg h-8.5 w-8.5 flex items-center justify-center p-0 shadow-none border-0 transition-all active:scale-95"
    >
      <FilePdf className="size-[18px] text-black shrink-0" weight="regular" />
    </TooltipIconButton>
  );
};

const ScrollToBottomButton: FC = () => {
  const [showButton, setShowButton] = useState(false);

  useEffect(() => {
    const handleScroll = (e: Event) => {
      const target = e.target as HTMLElement | null;
      if (!target || !target.classList.contains('chat-scrollbar')) return;
      const distanceFromBottom =
        target.scrollHeight - target.scrollTop - target.clientHeight;
      // Only show button if user has scrolled up by more than 160px
      setShowButton(distanceFromBottom > 160);
    };

    window.addEventListener('scroll', handleScroll, true);
    return () => window.removeEventListener('scroll', handleScroll, true);
  }, []);

  if (!showButton) return null;

  return (
    <ThreadPrimitive.ScrollToBottom asChild>
      <Button
        type="button"
        variant="outline"
        size="icon-sm"
        aria-label="Scroll to latest"
        className="absolute bottom-4 right-4 h-9 w-9 rounded-full bg-background hover:bg-muted text-foreground border border-border/80 shadow-none flex items-center justify-center p-0 disabled:hidden transition-all duration-200 active:scale-95 animate-in fade-in"
      >
        <ArrowDown size={16} weight="bold" />
      </Button>
    </ThreadPrimitive.ScrollToBottom>
  );
};

const DefaultEmpty: FC = () => (
  <div
    className="flex flex-col items-center justify-center text-center max-w-xl px-10 py-12 mx-auto select-none rounded-3xl border-2 border-dashed border-slate-200/90 bg-transparent shadow-none"
    style={{ fontFamily: 'var(--font-google-sans)' }}
  >
    <p className="text-[20px] text-slate-800 font-normal leading-relaxed">
      Upload your papers and ask questions
    </p>
    <span className="my-3 text-[13px] uppercase tracking-widest text-slate-400 font-medium">
      OR
    </span>
    <p className="text-[20px] text-slate-800 font-normal leading-relaxed">
      Search our{' '}
      <span className="text-[#d63384] font-medium">knowledge base</span> for
      answers.
    </p>
  </div>
);

const CitationLink: FC<{
  href?: string;
  children?: ReactNode;
  className?: string;
}> = ({ href, children, ...rest }) => {
  const onCitationClick = useContext(CitationClickContext);
  if (typeof href === 'string' && href.startsWith('#cite-')) {
    const chunkId = href.slice('#cite-'.length);
    // References list links have long text like "Vegetation data collection — file (p. 2)"
    // — render as clean pink text. Inline badges are single digits like "1" — render as pill.
    const label =
      typeof children === 'string'
        ? children
        : Array.isArray(children)
          ? children.join('')
          : '';
    const isBadge =
      typeof label === 'string' &&
      /^\d+$/.test(label.trim()) &&
      label.trim().length <= 2;
    if (isBadge) {
      return (
        <button
          type="button"
          onClick={(e) => {
            e.preventDefault();
            onCitationClick?.(chunkId);
          }}
          className="inline-flex items-center justify-center min-w-[18px] px-1 mx-0.5 text-[11px] font-semibold leading-none rounded-full bg-[#ffecf6] text-[#d63384] border border-[#fbcfe8] hover:bg-[#d63384] hover:text-white transition-all cursor-pointer select-none align-super shadow-none outline-none"
          style={{ fontFamily: 'var(--font-google-sans)' }}
          title="View source"
          aria-label="View source for citation"
        >
          {children}
        </button>
      );
    }
    return (
      <button
        type="button"
        onClick={(e) => {
          e.preventDefault();
          onCitationClick?.(chunkId);
        }}
        className="text-left text-[14px] font-medium leading-relaxed text-[#d63384] hover:text-[#a01e5a] hover:underline underline-offset-2 transition-colors cursor-pointer select-text bg-transparent border-0 p-0 m-0 inline align-baseline shadow-none outline-none"
        style={{ fontFamily: 'var(--font-google-sans)' }}
        title="View source"
        aria-label="View source for citation"
      >
        {children}
      </button>
    );
  }
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-blue-600 hover:text-blue-800 hover:underline font-medium"
      style={{ fontFamily: 'var(--font-google-sans)' }}
      {...rest}
    >
      {children}
    </a>
  );
};

const markdownComponents = memoizeMarkdownComponents({
  a: CitationLink,
  hr: () => <hr className="my-4 border-0 border-t border-slate-200" />,
  p: ({ children }) => (
    <p
      className="mb-3.5 last:mb-0 leading-[1.7] text-[17px] text-slate-800 font-normal"
      style={{ fontFamily: 'var(--font-google-sans)' }}
    >
      {children}
    </p>
  ),
  h1: ({ children }) => (
    <h1
      className="text-[22px] font-bold text-slate-900 mt-4 mb-2 first:mt-0 tracking-tight"
      style={{ fontFamily: 'var(--font-google-sans)' }}
    >
      {children}
    </h1>
  ),
  h2: ({ children }) => (
    <h2
      className="text-[19.5px] font-bold text-slate-900 mt-3.5 mb-1.5 first:mt-0 tracking-tight"
      style={{ fontFamily: 'var(--font-google-sans)' }}
    >
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3
      className="text-[17.5px] font-semibold text-slate-900 mt-3 mb-1 first:mt-0 tracking-tight"
      style={{ fontFamily: 'var(--font-google-sans)' }}
    >
      {children}
    </h3>
  ),
  ul: ({ children }) => (
    <ul
      className="list-disc pl-5 mb-3.5 space-y-1.5 text-[17px] leading-[1.7] text-slate-800"
      style={{ fontFamily: 'var(--font-google-sans)' }}
    >
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol
      className="list-decimal pl-5 mb-3.5 space-y-1.5 text-[17px] leading-[1.7] text-slate-800"
      style={{ fontFamily: 'var(--font-google-sans)' }}
    >
      {children}
    </ol>
  ),
  li: ({ children }) => <li className="leading-[1.7]">{children}</li>,
  strong: ({ children }) => (
    <strong className="font-semibold text-slate-900">{children}</strong>
  ),
  b: ({ children }) => (
    <strong className="font-semibold text-slate-900">{children}</strong>
  ),
  em: ({ children }) => <em className="italic">{children}</em>,
  i: ({ children }) => <em className="italic">{children}</em>,
  u: ({ children }) => (
    <u className="underline underline-offset-2">{children}</u>
  ),
  del: ({ children }) => (
    <del className="line-through text-slate-500">{children}</del>
  ),
  s: ({ children }) => (
    <s className="line-through text-slate-500">{children}</s>
  ),
  sub: ({ children }) => (
    <sub className="text-[75%] leading-none align-sub">{children}</sub>
  ),
  sup: ({ children }) => (
    <sup className="text-[75%] leading-none align-super">{children}</sup>
  ),
  mark: ({
    children,
    className,
    id,
  }: {
    children?: ReactNode;
    className?: string;
    id?: string;
  }) => (
    <mark
      className={className ?? 'bg-yellow-100 text-slate-900 rounded px-1'}
      id={id}
    >
      {children}
    </mark>
  ),
  code: ({ children }) => (
    <code className="font-mono text-[14px] bg-slate-100/90 text-slate-800 px-1.5 py-0.5 rounded border border-slate-200/60">
      {children}
    </code>
  ),
  blockquote: ({ children }) => (
    <blockquote
      className="border-l-2 border-[#ff6dba] pl-3.5 italic text-slate-600 my-3 text-[16.5px]"
      style={{ fontFamily: 'var(--font-google-sans)' }}
    >
      {children}
    </blockquote>
  ),
  table: ({ children }) => (
    <div className="my-4 w-full overflow-x-auto rounded-xl border border-slate-200">
      <table className="w-full border-collapse text-[15px] text-left text-slate-800">
        {children}
      </table>
    </div>
  ),
  thead: ({ children }) => (
    <thead className="bg-slate-50 border-b border-slate-200 text-slate-900 font-semibold">
      {children}
    </thead>
  ),
  tbody: ({ children }) => (
    <tbody className="divide-y divide-slate-200/70 bg-white">{children}</tbody>
  ),
  tr: ({ children }) => (
    <tr className="transition-colors hover:bg-slate-50/50">{children}</tr>
  ),
  th: ({ children }) => (
    <th className="px-4 py-3 font-semibold text-slate-900 border-r border-slate-200 last:border-r-0">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="px-4 py-3 text-slate-800 border-r border-slate-200/70 last:border-r-0 align-top leading-relaxed">
      {children}
    </td>
  ),
});

const remarkPlugins = [remarkGfm];
const rehypePlugins = [rehypeRaw];

// Module-level so its identity never changes across renders — a
// Strip leaked backend [cN] markers only (the `c` prefix is required).
// Bare [N] is left alone: it can be a legit footnote/list/item number in
// model prose. Long `[display](#cite-cid)` References links pass through
// (their label isn't bare digits).
function stripInlineMarkers(text: string): string {
  return text.replace(/\[\s*[Cc]\s*(\d+)\s*\]/g, '');
}

const MarkdownText: FC = () => {
  return (
    <MarkdownTextPrimitive
      smooth={false}
      remarkPlugins={remarkPlugins}
      rehypePlugins={rehypePlugins}
      components={markdownComponents}
      preprocess={stripInlineMarkers}
      className="w-full text-slate-800 text-[17px] leading-[1.7]"
    />
  );
};

const UserMessage: FC = () => {
  const content = useAuiState((s) => s.message.content);
  const text = readMessageContent(content);

  return (
    <MessagePrimitive.Root className="mx-auto w-full max-w-[var(--thread-max-width)] flex flex-col items-end group pt-[var(--turn-gap-answer-to-prompt)] first:pt-0 pb-[var(--turn-gap-prompt-to-answer)]">
      <ComposerPrimitive.If editing>
        <UserEditComposer />
      </ComposerPrimitive.If>

      <ComposerPrimitive.If editing={false}>
        <div className="flex items-center gap-2 w-full justify-end">
          <UserActionBar />

          <div
            className="max-w-[85%] rounded-[22px] px-5 py-3.5 text-[17px] leading-relaxed text-slate-900 break-words whitespace-pre-wrap select-text shadow-none"
            style={{
              backgroundColor: PINK_USER_BG,
              fontFamily: 'var(--font-google-sans)',
            }}
          >
            {text}
          </div>
        </div>

        <BranchPicker />
      </ComposerPrimitive.If>
    </MessagePrimitive.Root>
  );
};

const UserEditComposer: FC = () => (
  <ComposerPrimitive.Root className="w-full max-w-2xl">
    <div className="flex flex-col gap-2.5 rounded-2xl border border-slate-200 bg-white p-3.5 shadow-none">
      <ComposerPrimitive.Input asChild>
        <Textarea
          className="min-h-[60px] max-h-[200px] resize-none border-0 bg-transparent py-2 pl-1 pr-2 !text-[18px] md:!text-[18px] text-slate-900 leading-relaxed shadow-none focus-visible:ring-0 focus-visible:ring-offset-0 chat-scrollbar outline-none overflow-x-hidden [overflow-wrap:anywhere] break-words [field-sizing:normal]"
          style={{ fontFamily: 'var(--font-google-sans)' }}
          autoFocus
        />
      </ComposerPrimitive.Input>
      <div className="flex justify-end gap-2">
        <ComposerPrimitive.Cancel asChild>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="rounded-full px-4 text-[14px] font-medium text-slate-600 hover:bg-slate-100"
            style={{ fontFamily: 'var(--font-google-sans)' }}
          >
            Cancel
          </Button>
        </ComposerPrimitive.Cancel>
        <ComposerPrimitive.Send asChild>
          <Button
            type="submit"
            size="sm"
            className="rounded-full px-4 text-[14px] font-medium text-white hover:opacity-90 transition-opacity"
            style={{
              backgroundColor: PINK_ACCENT,
              fontFamily: 'var(--font-google-sans)',
            }}
          >
            Send
          </Button>
        </ComposerPrimitive.Send>
      </div>
    </div>
  </ComposerPrimitive.Root>
);

const UserActionBar: FC = () => (
  <ActionBarPrimitive.Root className="flex items-center self-end mb-1 opacity-0 transition-opacity duration-150 group-hover:opacity-100 focus-within:opacity-100 data-[floating=true]:opacity-100">
    <ActionBarPrimitive.Edit asChild>
      <TooltipIconButton
        type="button"
        variant="ghost"
        size="icon-sm"
        tooltip="Edit"
        className="text-black hover:text-black hover:bg-slate-100 rounded-lg h-8.5 w-8.5 flex items-center justify-center p-0 shadow-none border-0 transition-all active:scale-95"
      >
        <PencilSimple
          className="size-[18px] text-black shrink-0"
          weight="regular"
        />
      </TooltipIconButton>
    </ActionBarPrimitive.Edit>
  </ActionBarPrimitive.Root>
);

function readMessageContent(
  content: readonly { type: string; text?: string }[] | undefined,
): string {
  if (!content) return '';
  return content
    .map((part) => (part.type === 'text' ? (part.text ?? '') : ''))
    .join('')
    .trim();
}

const AssistantMessage: FC = () => {
  const onCitationClick = useContext(ThreadCitationClickContext);
  const customData = (useAuiState((s) => s.message.metadata?.custom) ??
    {}) as RagMessageCustomData;
  const sources = customData.sources ?? [];
  const citations = customData.citations ?? [];
  const content = useAuiState((s) => s.message.content);
  const status = useAuiState((s) => s.message.status);
  const text = readMessageContent(content);
  // Show the typing dot only when the message is actively streaming AND
  // has no text yet. A completed message is never treated as pending even
  // if text is briefly empty during an answer_corrected frame swap —
  // that race previously caused the content to vanish and show the dot.
  const isStreaming = status?.type === 'running';
  const isPending = isStreaming && !text;

  const handleCitationClick = useCallback(
    (chunkId: string) => {
      const source = sources.find((s) => s.chunk_id === chunkId);
      const citation = citations.find((c) => c.chunk_id === chunkId);
      onCitationClick?.({ chunkId, source, citation });
    },
    [sources, citations, onCitationClick],
  );

  return (
    <CitationClickContext.Provider value={handleCitationClick}>
      <MessagePrimitive.Root className="mx-auto w-full max-w-[var(--thread-max-width)] flex flex-col items-start group">
        {isPending ? (
          <div className="py-2.5 px-1 flex items-center">
            <span className="h-3.5 w-3.5 rounded-full bg-foreground animate-typing-dot" />
          </div>
        ) : (
          <>
            <div
              className="w-full text-slate-800 leading-relaxed text-[17px] pt-0"
              style={{ fontFamily: 'var(--font-google-sans)' }}
            >
              <MessagePrimitive.Content components={{ Text: MarkdownText }} />
            </div>

            <AssistantActionBar />

            <BranchPicker />
          </>
        )}
      </MessagePrimitive.Root>
    </CitationClickContext.Provider>
  );
};

const threadComponents = {
  UserMessage,
  AssistantMessage,
};

const AssistantActionBar: FC = () => (
  <ActionBarPrimitive.Root
    hideWhenRunning
    autohide="never"
    className="mt-3.5 mb-4 flex items-center gap-1.5 opacity-100"
  >
    <ActionBarPrimitive.Copy asChild>
      <TooltipIconButton
        type="button"
        variant="ghost"
        size="icon-sm"
        tooltip="Copy"
        className="text-black hover:text-black hover:bg-slate-100 rounded-lg h-8.5 w-8.5 flex items-center justify-center p-0 shadow-none border-0 transition-all active:scale-95"
      >
        <MessagePrimitive.If copied>
          <Check
            className="size-[18px] text-slate-700 shrink-0"
            weight="bold"
          />
        </MessagePrimitive.If>
        <MessagePrimitive.If copied={false}>
          <Copy className="size-[18px] text-black shrink-0" weight="regular" />
        </MessagePrimitive.If>
      </TooltipIconButton>
    </ActionBarPrimitive.Copy>

    <ActionBarPrimitive.Reload asChild>
      <TooltipIconButton
        type="button"
        variant="ghost"
        size="icon-sm"
        tooltip="Regenerate"
        className="text-black hover:text-black hover:bg-slate-100 rounded-lg h-8.5 w-8.5 flex items-center justify-center p-0 shadow-none border-0 transition-all active:scale-95"
      >
        <ArrowClockwise
          className="size-[18px] text-black shrink-0"
          weight="regular"
        />
      </TooltipIconButton>
    </ActionBarPrimitive.Reload>

    <ExportAnswerPdfButton />
  </ActionBarPrimitive.Root>
);

const BranchPicker: FC = () => (
  <MessagePrimitive.If hasBranches>
    <BranchPickerPrimitive.Root
      hideWhenSingleBranch
      className="mt-3.5 mr-1 inline-flex items-center gap-1 text-[13px] font-medium text-black"
    >
      <BranchPickerPrimitive.Previous asChild>
        <TooltipIconButton
          type="button"
          variant="ghost"
          size="icon-sm"
          className="h-7 w-7 rounded-full text-black hover:text-black hover:bg-slate-100 p-0 shadow-none border-0 transition-all active:scale-95"
          tooltip="Previous branch"
        >
          <CaretLeft className="size-4 text-black shrink-0" weight="bold" />
        </TooltipIconButton>
      </BranchPickerPrimitive.Previous>
      <span className="tabular-nums px-1.5 text-black select-none">
        <BranchPickerPrimitive.Number /> / <BranchPickerPrimitive.Count />
      </span>
      <BranchPickerPrimitive.Next asChild>
        <TooltipIconButton
          type="button"
          variant="ghost"
          size="icon-sm"
          className="h-7 w-7 rounded-full text-black hover:text-black hover:bg-slate-100 p-0 shadow-none border-0 transition-all active:scale-95"
          tooltip="Next branch"
        >
          <CaretRight className="size-4 text-black shrink-0" weight="bold" />
        </TooltipIconButton>
      </BranchPickerPrimitive.Next>
    </BranchPickerPrimitive.Root>
  </MessagePrimitive.If>
);

const Composer: FC = () => (
  <ComposerPrimitive.Root className="bg-transparent px-4 pb-6 pt-4">
    <div className="mx-auto flex max-w-[var(--thread-max-width)] items-end gap-3 rounded-[28px] border border-slate-200/90 bg-white py-2 pl-5 pr-2 shadow-none focus-within:border-slate-400/90 transition-all">
      <ComposerPrimitive.Input asChild>
        <Textarea
          rows={1}
          autoFocus
          placeholder="Ask anything..."
          className="min-h-[36px] max-h-[190px] flex-1 resize-none rounded-none border-0 bg-transparent py-1.5 pl-0 pr-2 !text-[18px] md:!text-[18px] text-slate-900 leading-normal shadow-none focus-visible:ring-0 focus-visible:ring-offset-0 placeholder:text-slate-400 placeholder:!text-[18px] md:placeholder:!text-[18px] chat-scrollbar outline-none overflow-x-hidden [overflow-wrap:anywhere] break-words [field-sizing:normal]"
          style={{ fontFamily: 'var(--font-google-sans)' }}
        />
      </ComposerPrimitive.Input>
      <div className="flex items-center shrink-0 pb-0.5">
        <ThreadPrimitive.If running>
          <ComposerPrimitive.Cancel asChild>
            <TooltipIconButton
              type="button"
              variant="default"
              size="icon"
              side="top"
              tooltip="Stop"
              className="h-10 w-10 rounded-full text-white shadow-none active:scale-95 hover:opacity-90 flex items-center justify-center p-0 shrink-0 border-0 transition-all cursor-pointer"
              style={{ backgroundColor: PINK_ACCENT }}
            >
              <span className="h-3.5 w-3.5 rounded-[3px] bg-white" />
            </TooltipIconButton>
          </ComposerPrimitive.Cancel>
        </ThreadPrimitive.If>
        <ThreadPrimitive.If running={false}>
          <ComposerPrimitive.Send asChild>
            <TooltipIconButton
              type="submit"
              size="icon"
              side="top"
              tooltip="Send"
              className="h-10 w-10 rounded-full text-white shadow-none active:scale-95 disabled:opacity-30 disabled:cursor-not-allowed hover:opacity-90 flex items-center justify-center p-0 shrink-0 border-0 transition-all cursor-pointer"
              style={{ backgroundColor: PINK_ACCENT }}
            >
              <ArrowUp className="size-5.5 text-white" weight="bold" />
            </TooltipIconButton>
          </ComposerPrimitive.Send>
        </ThreadPrimitive.If>
      </div>
    </div>
  </ComposerPrimitive.Root>
);
