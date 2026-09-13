/**
 * Client-side vector PDF generation for chat Q&A pairs via the
 * browser print engine. Strips citation markers, compiles Markdown to
 * sanitized HTML, and dispatches to a print-ready iframe.
 */

import { marked } from 'marked';
import DOMPurify from 'dompurify';

// Configures marked for GitHub-flavored markdown and preserved newlines.
marked.setOptions({ gfm: true, breaks: true });

// ─── Citation stripping ────────────────────────────────────────────────────
function stripCitations(text: string): string {
  return text.replace(/\[c\d+\](?:\s*\[c\d+\])*/g, '').trim();
}

// ─── Markdown → safe HTML ──────────────────────────────────────────────────
const ALLOWED_TAGS = [
  'p',
  'br',
  'span',
  'strong',
  'b',
  'em',
  'i',
  'del',
  's',
  'u',
  'sub',
  'sup',
  'mark',
  'code',
  'pre',
  'ul',
  'ol',
  'li',
  'blockquote',
  'hr',
  'h1',
  'h2',
  'h3',
  'h4',
  'h5',
  'h6',
  'a',
  'table',
  'thead',
  'tbody',
  'tr',
  'th',
  'td',
];

function markdownToSafeHtml(md: string): string {
  if (!md) return '';
  const stripped = stripCitations(md);
  if (!stripped) return '';
  // Evaluates synchronous Markdown tokenization into HTML.
  const raw = marked.parse(stripped, { async: false }) as string;
  return DOMPurify.sanitize(raw, {
    ALLOWED_TAGS,
    ALLOWED_ATTR: ['href', 'title'],
  });
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function suggestedFilename(iso: string): string {
  const d = new Date(iso);
  const stamp = Number.isNaN(d.getTime()) ? new Date() : d;
  const pad = (n: number) => String(n).padStart(2, '0');
  const date = `${stamp.getFullYear()}-${pad(stamp.getMonth() + 1)}-${pad(stamp.getDate())}`;
  const time = `${pad(stamp.getHours())}-${pad(stamp.getMinutes())}`;
  return `${date},${time},bloomindex`;
}

interface Pair {
  q: string;
  a: string;
}

function renderHtml(pairs: readonly Pair[], suggestedName: string): string {
  const body = pairs
    .map((p, i) => {
      const q = escapeHtml(stripCitations(p.q));
      const aHtml = markdownToSafeHtml(p.a);
      const sep = i < pairs.length - 1 ? '<div class="sep">**</div>' : '';
      return `<section class="pair">
${q ? `<h1>${q}</h1>` : ''}
${aHtml ? `<div class="answer">${aHtml}</div>` : ''}
</section>
${sep}`;
    })
    .join('\n');

  // Avoid page breaks directly after questions to prevent orphaned headers.
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>${escapeHtml(suggestedName)}</title>
<style>
@page { size: A4; margin: 20mm; }
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; color: #000; }
body {
  font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI",
               "Helvetica Neue", Arial, system-ui, "Liberation Sans",
               "DejaVu Sans", sans-serif;
  font-size: 14pt;
  line-height: 1.45;
  -webkit-print-color-adjust: exact;
  print-color-adjust: exact;
}
h1 {
  font-size: 22pt;
  font-weight: 700;
  margin: 0 0 8pt 0;
  page-break-after: avoid;
  line-height: 1.25;
}
.answer { font-size: 14pt; }
.answer p { margin: 0 0 8pt 0; }
.answer p:last-child { margin-bottom: 0; }
.answer strong, .answer b { font-weight: 700; }
.answer em, .answer i { font-style: italic; }
.answer u { text-decoration: underline; }
.answer del, .answer s { text-decoration: line-through; }
.answer sub { vertical-align: sub; font-size: 75%; line-height: 0; }
.answer sup { vertical-align: super; font-size: 75%; line-height: 0; }
.answer mark { background: #fef08a; padding: 1pt 3pt; border-radius: 2pt; }
.answer h1, .answer h2, .answer h3, .answer h4, .answer h5, .answer h6 {
  font-weight: 700;
  margin: 12pt 0 6pt 0;
  page-break-after: avoid;
  line-height: 1.3;
}
.answer h1 { font-size: 17pt; }
.answer h2 { font-size: 16pt; }
.answer h3 { font-size: 15pt; }
.answer h4, .answer h5, .answer h6 { font-size: 14pt; }
.answer ul, .answer ol { margin: 0 0 8pt 0; padding-left: 24pt; }
.answer li { margin: 0 0 2pt 0; }
.answer li > p { margin: 0 0 4pt 0; }
.answer code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas,
               "Liberation Mono", monospace;
  font-size: 12.5pt;
  background: #f3f4f6;
  padding: 1pt 4pt;
  border-radius: 3pt;
}
.answer pre {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas,
               "Liberation Mono", monospace;
  font-size: 12.5pt;
  background: #f3f4f6;
  padding: 8pt 10pt;
  border-radius: 4pt;
  margin: 0 0 8pt 0;
  white-space: pre-wrap;
  word-break: break-word;
}
.answer pre code { background: transparent; padding: 0; border-radius: 0; }
.answer blockquote {
  margin: 0 0 8pt 0;
  padding: 0 0 0 12pt;
  border-left: 3pt solid #cbd5e1;
  color: #4b5563;
}
.answer hr {
  border: 0;
  border-top: 1pt solid #cbd5e1;
  margin: 12pt 0;
}
.answer a { color: #2563eb; text-decoration: underline; }
.answer table {
  border-collapse: collapse;
  margin: 0 0 8pt 0;
  font-size: 13pt;
}
.answer th, .answer td {
  border: 1pt solid #cbd5e1;
  padding: 4pt 6pt;
  text-align: left;
  vertical-align: top;
}
.answer th { background: #f3f4f6; font-weight: 700; }
.sep {
  font-size: 14pt;
  text-align: center;
  /* Dual line-height spacing between adjacent Q&A blocks. */
  margin: 18pt 0 36pt 0;
}
</style>
</head>
<body>
${body}
</body>
</html>`;
}

function openPrintWindow(suggestedName: string, html: string): void {
  if (typeof document === 'undefined') return;

  const iframe = document.createElement('iframe');
  iframe.setAttribute('aria-hidden', 'true');
  iframe.style.cssText =
    'position:fixed;right:0;bottom:0;width:0;height:0;border:0;visibility:hidden;';
  document.body.appendChild(iframe);

  const doc = iframe.contentDocument;
  if (!doc) {
    document.body.removeChild(iframe);
    return;
  }

  doc.open();
  doc.write(html);
  doc.close();

  try {
    doc.title = suggestedName;
  } catch {
    // Sandboxed iframes may throw on document title mutations.
  }

  let removed = false;
  const removeIframe = () => {
    if (removed) return;
    removed = true;
    try {
      document.body.removeChild(iframe);
    } catch {
      // Guard against repeated removal invocations.
    }
  };

  iframe.contentWindow?.addEventListener('afterprint', removeIframe, {
    once: true,
  });

  const fallbackTimer = window.setTimeout(removeIframe, 60_000);
  iframe.contentWindow?.addEventListener(
    'afterprint',
    () => window.clearTimeout(fallbackTimer),
    { once: true },
  );

  window.setTimeout(() => {
    try {
      iframe.contentWindow?.focus();
      iframe.contentWindow?.print();
    } catch (err) {
      console.error('[exportPdf] print() failed:', err);
      removeIframe();
    }
  }, 80);
}

export interface AnswerExportPayload {
  question: string;
  answer: string;
  /** ISO timestamp used as the default filename suggestion. */
  timestamp?: string;
}

export function exportAnswerAsPdf(payload: AnswerExportPayload): void {
  const ts = suggestedFilename(payload.timestamp ?? new Date().toISOString());
  const html = renderHtml([{ q: payload.question, a: payload.answer }], ts);
  openPrintWindow(ts, html);
}

export interface ThreadTurn {
  role: 'user' | 'assistant';
  text: string;
}

export interface ThreadExportPayload {
  turns: readonly ThreadTurn[];
  /** ISO timestamp used as the default filename suggestion. */
  timestamp?: string;
}

export function exportThreadAsPdf(payload: ThreadExportPayload): void {
  const ts = suggestedFilename(payload.timestamp ?? new Date().toISOString());

  // Preserves trailing questions lacking completed assistant responses.
  const turns = payload.turns;
  const pairs: Pair[] = [];
  for (let i = 0; i < turns.length; i++) {
    if (turns[i].role !== 'user') continue;
    const q = turns[i].text;
    const next = turns[i + 1];
    const a = next && next.role === 'assistant' ? next.text : '';
    pairs.push({ q, a });
    if (next && next.role === 'assistant') i++;
  }

  const html = renderHtml(pairs, ts);
  openPrintWindow(ts, html);
}
