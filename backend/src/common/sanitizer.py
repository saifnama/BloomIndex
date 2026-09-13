"""HTML sanitization and link security normalization.

Protects against XSS attacks in rendered content by stripping
disallowed elements and enforcing secure rel attributes on external
links.
"""

import re

try:
    import nh3

    _NH3_AVAILABLE = True
except Exception:
    nh3 = None
    _NH3_AVAILABLE = False


def sanitize(html_content: str) -> str:
    """Sanitize untrusted HTML markup against an explicit allowlist.

    Uses nh3 (Ammonia) when available to strip disallowed tags and
    attributes while securing target="_blank" links with rel attributes.
    Falls back to regex-based stripping when nh3 is missing.
    """
    if html_content is None:
        return ""
    text = html_content.strip()
    if not text:
        return ""

    allowed_tags = {
        "p",
        "h2",
        "h3",
        "table",
        "tr",
        "td",
        "th",
        "thead",
        "tbody",
        "caption",
        "figure",
        "figcaption",
        "img",
        "a",
        "span",
        "cite",
        "em",
        "strong",
        "sub",
        "sup",
        "br",
        "div",
        "code",
        "blockquote",
        "ul",
        "ol",
        "li",
        "section",
    }
    allowed_attributes = {
        "cite": {"data-rid"},
        "a": {"href", "target", "rel", "class"},
        "img": {"src", "alt", "title", "width", "height", "loading"},
        "td": {"colspan", "rowspan"},
        "th": {"colspan", "rowspan", "scope"},
        "span": {"class"},
        "h3": {"id", "class"},
        "h2": {"id", "class"},
        "div": {"class"},
        "figure": {"class"},
        "ol": {"type", "style"},
        "li": {"style"},
    }

    if _NH3_AVAILABLE and nh3 is not None:
        cleaned = nh3.clean(
            text,
            tags=allowed_tags,
            attributes=allowed_attributes,
            link_rel=None,
        )
    else:
        # Fall back to regex stripping of scripts and javascript schemes
        # when the compiled Rust sanitizer (nh3) is not installed.
        s = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.S | re.I)
        s = re.sub(r'href=["\']javascript:[^"\']+["\']', 'href=""', s, flags=re.I)
        cleaned = s

    def _normalize_anchor_rel(match: re.Match[str]) -> str:
        """Ensure links opening new tabs include noopener and noreferrer.

        Prevents tab-nabbing and window.opener access from untrusted
        external destinations.
        """
        tag = match.group(0)
        target_match = re.search(r'target=("|\')([^"\']*)(\1)', tag, flags=re.I)
        if not target_match or target_match.group(2).lower() != "_blank":
            return tag

        rel_tokens = ["noopener", "noreferrer"]
        rel_match = re.search(r'rel=("|\')([^"\']*)(\1)', tag, flags=re.I)
        if rel_match:
            existing_tokens = rel_match.group(2).split()
            merged_tokens = []
            for token in [*existing_tokens, *rel_tokens]:
                normalized = token.strip().lower()
                if normalized and normalized not in merged_tokens:
                    merged_tokens.append(normalized)
            replacement = f'rel="{" ".join(merged_tokens)}"'
            return re.sub(r'rel=("|\')[^"\']*(\1)', replacement, tag, flags=re.I)

        insert_at = target_match.end()
        return f'{tag[:insert_at]} rel="noopener noreferrer"{tag[insert_at:]}'

    cleaned = re.sub(r"<a\b[^>]*>", _normalize_anchor_rel, cleaned, flags=re.I)
    # Normalize empty or completely stripped output to empty string.
    return cleaned or ""
