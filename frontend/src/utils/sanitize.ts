import DOMPurify from 'dompurify';

/**
 * Sanitizes server-rendered HTML against an allowlist mirroring
 * backend rules before injection into dangerouslySetInnerHTML.
 */
export function sanitizeHtml(html: string): string {
  if (typeof window === 'undefined' || !html) return html ?? '';

  // Decodes HTML entities into literal markup before purification.
  let decoded = html;
  if (typeof document !== 'undefined') {
    const textarea = document.createElement('textarea');
    textarea.innerHTML = html;
    decoded = textarea.value;
  }

  // Tag allowlist aligned with backend sanitizer.py rules.
  const ALLOWED_TAGS = [
    'p',
    'h2',
    'h3',
    'table',
    'tr',
    'td',
    'th',
    'thead',
    'tbody',
    'caption',
    'figure',
    'figcaption',
    'img',
    'a',
    'span',
    'mark',
    'cite',
    'em',
    'strong',
    'i',
    'b',
    'sub',
    'sup',
    'br',
    'div',
    'code',
    'blockquote',
    'ul',
    'ol',
    'li',
    'section',
  ];
  // Attributes required for NER highlighting and reference links.
  const PURIFY_CONFIG: import('dompurify').Config = {
    ALLOWED_TAGS,
    ADD_ATTR: [
      'id',
      'class',
      'style',
      'data-rid',
      'data-entity',
      // Botanical NER metadata attached by the backend highlighter.
      'data-accepted-scientific-name',
      'data-scientific-name-verified',
      'data-common-name',
      'data-name-type',
      'data-taxon-id',
      'data-source-db',
      'data-source-url',
    ],
  };
  try {
    return DOMPurify.sanitize(decoded, PURIFY_CONFIG);
  } catch {
    // Return an empty string on failure to block unsafe content.
    return '';
  }
}

/**
 * Sanitizes title and summary text while preserving scientific markup
 * (e.g. genus/species italics, chemical formulas).
 */
export function formatTextWithFormatting(
  text: string | null | undefined,
): string {
  if (!text) return '';
  if (typeof document === 'undefined') return text;

  try {
    const textarea = document.createElement('textarea');
    textarea.innerHTML = text;
    const decoded = textarea.value;

    const parser = new DOMParser();
    const doc = parser.parseFromString(`<div>${decoded}</div>`, 'text/html');
    const div = doc.querySelector('div');
    if (!div) return text;

    // Strips executable elements and interactive inputs.
    const dangerous = div.querySelectorAll(
      'script, iframe, object, embed, link, style, form, input, button, textarea, select',
    );
    dangerous.forEach((el) => el.remove());

    let result = div.innerHTML;

    // Prunes vacant inline formatting containers.
    result = result.replace(/<(i|b|strong|em|sub|sup)>\s*<\/\1>/gi, '');
    result = result.replace(/\s+/g, ' ').trim();

    return result;
  } catch {
    // Strips executable markup and tags on DOMParser failure.
    const textarea = document.createElement('textarea');
    textarea.innerHTML = text;
    return textarea.value
      .replace(/<script[^>]*>.*?<\/script>/gi, '')
      .replace(/<[^>]+>/g, '')
      .replace(/\s+/g, ' ')
      .trim();
  }
}
