"""Europe PMC XML parser and JATS-to-HTML converter.

Provides XML namespace normalization, JATS element conversion to
semantic HTML, bibliographic metadata extraction, section parsing, and
hierarchical table-of-contents generation.
"""

import logging
import re
from typing import Any

from bs4 import BeautifulSoup
from lxml import etree as ET

logger = logging.getLogger(__name__)


class JATSConverter:
    """Convert JATS XML tags into semantic HTML markup."""

    @staticmethod
    def inline_to_html(text: str) -> str:
        """Convert JATS inline formatting tags in text to semantic HTML.

        Transforms markup elements in abstracts and metadata, mapping
        italic to em, bold to strong, and resolving link attributes.

        Args:
            text: Raw string containing inline JATS XML tags.

        Returns:
            Sanitized HTML string with normalized markup tags.
        """
        if not text:
            return ""

        soup = BeautifulSoup(text, "html.parser")

        # Map standard JATS inline elements to equivalent HTML tags.
        TAG_MAP = {
            "italic": "em",
            "bold": "strong",
            "sup": "sup",
            "sub": "sub",
            "monospace": "code",
        }
        for jats_tag, html_tag in TAG_MAP.items():
            for tag in soup.find_all(jats_tag):
                tag.name = html_tag

        # Transform small-caps tags into styled span elements.
        for tag in soup.find_all("sc"):
            tag.name = "span"
            tag.attrs = {"class": "small-caps"}

        # Convert external links to outbound anchor elements.
        for tag in soup.find_all("ext-link"):
            tag.name = "a"
            href = tag.get("xlink:href", "") or tag.get("{http://www.w3.org/1999/xlink}href", "")
            tag.attrs = {"href": href, "target": "_blank", "rel": "noopener"} if href else {}

        # Wrap email elements in mailto anchor hyperlinks.
        for tag in soup.find_all("email"):
            addr = tag.get_text(strip=True)
            tag.name = "a"
            tag.attrs = {"href": f"mailto:{addr}"}
            tag.string = addr

        # Strip non-standard attributes from paragraph elements.
        for tag in soup.find_all("p"):
            tag.attrs = {}

        return str(soup)

    @staticmethod
    def ensure_xmlns(xml_content: str) -> str:
        """Ensure xmlns:xlink is declared at root level for lxml parsing.

        Declares the XLink namespace on root article tags when present
        only on descendants, preventing parser namespace errors.

        Args:
            xml_content: Raw JATS XML text string.

        Returns:
            Normalized XML string with root namespace declaration.
        """
        if "xlink:" in xml_content and "xmlns:xlink" not in xml_content.split(">", 1)[0]:
            # Declare xlink namespace in root article opening tag.
            return xml_content.replace(
                "<article ",
                '<article xmlns:xlink="http://www.w3.org/1999/xlink" ',
                1,
            )
        return xml_content

    @staticmethod
    def clean_xml(xml_content: str) -> str:
        """Strip XML markup tags and return normalized plain text."""
        try:
            root = ET.fromstring(JATSConverter.ensure_xmlns(xml_content).encode("utf-8"))
            body = root.find(".//body")
            if body is not None:
                return "".join(body.itertext()).strip()
            return "".join(root.itertext()).strip()
        except Exception:
            clean = re.sub(r"<[^>]+>", " ", xml_content)
            return re.sub(r"\s+", " ", clean).strip()


class XMLParser:
    """Parse Europe PMC JATS XML into structured sections and metadata."""

    @staticmethod
    def extract_title_from_xml(xml_content: str) -> str:
        """Extract article title from PMC JATS XML."""
        try:
            root = ET.fromstring(JATSConverter.ensure_xmlns(xml_content).encode("utf-8"))
            title_node = root.find(".//article-title")
            if title_node is not None:
                return "".join(title_node.itertext()).strip()
        except Exception:
            pass
        return ""

    @staticmethod
    def extract_metadata_from_xml(xml_content: str) -> dict:
        """Extract title, authors, journal, and publication date in one pass.

        Args:
            xml_content: Raw JATS XML string.

        Returns:
            Dictionary containing title, authors, journal, and date.
        """
        result = {"title": "", "authors": [], "journal": "", "date": ""}
        try:
            root = ET.fromstring(JATSConverter.ensure_xmlns(xml_content).encode("utf-8"))

            title_node = root.find(".//article-title")
            if title_node is not None:
                result["title"] = "".join(title_node.itertext()).strip()

            authors = []
            for contrib in root.findall(".//contrib-group/contrib[@contrib-type='author']"):
                surname = contrib.findtext("name/surname", "").strip()
                given = contrib.findtext("name/given-names", "").strip()
                if surname:
                    authors.append(f"{given} {surname}".strip())
            if not authors:
                for contrib in root.findall(".//contrib-group/contrib"):
                    surname = contrib.findtext("name/surname", "").strip()
                    given = contrib.findtext("name/given-names", "").strip()
                    if surname:
                        authors.append(f"{given} {surname}".strip())
            result["authors"] = authors

            journal_title = root.findtext(".//journal-title", "").strip()
            if not journal_title:
                journal_title = root.findtext(".//abbrev-journal-title", "").strip()
            result["journal"] = journal_title

            for date_path in [
                ".//pub-date[@date-type='pub']/year",
                ".//pub-date/year",
                ".//article-meta/pub-date/year",
                ".//pub-date[@date-type='epub']/year",
                ".//pub-date[@date-type='collection']/year",
            ]:
                year_node = root.find(date_path)
                if year_node is not None and year_node.text:
                    year = year_node.text.strip()
                    parent = year_node.getparent()
                    if parent is not None:
                        month = parent.findtext("month", "").strip()
                        day = parent.findtext("day", "").strip()
                        if month and day:
                            from calendar import month_name

                            try:
                                month_str = month_name[int(month)]
                            except (ValueError, IndexError):
                                month_str = month
                            result["date"] = f"{day} {month_str} {year}"
                            break
                    result["date"] = year
                    break
        except Exception:
            pass
        return result

    @staticmethod
    def extract_authors_from_xml(xml_content: str) -> list:
        """Extract list of author names from PMC JATS XML."""
        try:
            root = ET.fromstring(JATSConverter.ensure_xmlns(xml_content).encode("utf-8"))
            authors = []
            for contrib in root.findall(".//contrib-group/contrib[@contrib-type='author']"):
                surname = contrib.findtext("name/surname", "").strip()
                given = contrib.findtext("name/given-names", "").strip()
                if surname:
                    authors.append(f"{given} {surname}".strip())
            # Match contributors when author types are absent.
            if not authors:
                for contrib in root.findall(".//contrib-group/contrib"):
                    surname = contrib.findtext("name/surname", "").strip()
                    given = contrib.findtext("name/given-names", "").strip()
                    if surname:
                        authors.append(f"{given} {surname}".strip())
            return authors
        except Exception:
            return []

    @staticmethod
    def extract_journal_from_xml(xml_content: str) -> str:
        """Extract journal publication title from PMC JATS XML."""
        try:
            root = ET.fromstring(JATSConverter.ensure_xmlns(xml_content).encode("utf-8"))
            journal_title = root.findtext(".//journal-title", "").strip()
            if journal_title:
                return journal_title
            # Fall back to abbreviated title when full name is missing.
            return root.findtext(".//abbrev-journal-title", "").strip()
        except Exception:
            return ""

    @staticmethod
    def extract_date_from_xml(xml_content: str) -> str:
        """Extract formatted publication date from PMC JATS XML."""
        try:
            root = ET.fromstring(JATSConverter.ensure_xmlns(xml_content).encode("utf-8"))
            # Check candidate date elements in document hierarchy.
            for date_path in [
                ".//pub-date[@date-type='pub']/year",
                ".//pub-date/year",
                ".//article-meta/pub-date/year",
                ".//pub-date[@date-type='epub']/year",
                ".//pub-date[@date-type='collection']/year",
            ]:
                year_node = root.find(date_path)
                if year_node is not None and year_node.text:
                    year = year_node.text.strip()
                    parent = year_node.getparent()
                    if parent is not None:
                        # Extract day and month if present.
                        month = parent.findtext("month", "").strip()
                        day = parent.findtext("day", "").strip()
                        if month and day:
                            from calendar import month_name

                            try:
                                month_str = month_name[int(month)]
                            except (ValueError, IndexError):
                                month_str = month
                            return f"{day} {month_str} {year}"
                    return year
            return ""
        except Exception:
            return ""

    @staticmethod
    def extract_toc_from_html(html_content: str) -> list[dict[str, Any]]:
        """Extract a two-level Table of Contents from semantic HTML.

        Collects h2 section headings and subsequent h3 sub-headings,
        structuring them into a hierarchical table-of-contents model.

        Args:
            html_content: Rendered semantic HTML string.

        Returns:
            List of top-level heading items with nested child headings.
        """
        if not html_content:
            return []

        try:
            soup = BeautifulSoup(html_content, "html.parser")
            toc: list[dict[str, Any]] = []

            for h2 in soup.find_all("h2"):
                h2_id = h2.get("id", "")
                h2_text = h2.get_text(strip=True)
                if not h2_id and not h2_text:
                    continue
                item: dict[str, Any] = {
                    "id": h2_id,
                    "title": h2_text,
                    "text": h2_text,
                    "level": 2,
                    "children": [],
                }

                # Collect following h3 siblings until the next h2
                sibling = h2.find_next_sibling()
                while sibling is not None and sibling.name != "h2":
                    if sibling.name == "h3":
                        sid = sibling.get("id", "")
                        stx = sibling.get_text(strip=True)
                        if sid or stx:
                            item["children"].append(
                                {"id": sid, "title": stx, "text": stx, "level": 3}
                            )
                    sibling = sibling.find_next_sibling()

                toc.append(item)

            # Promote h3 headings when document lacks h2 section tags.
            if not toc:
                for h3 in soup.find_all("h3"):
                    sid = h3.get("id", "")
                    stx = h3.get_text(strip=True)
                    if sid or stx:
                        toc.append(
                            {"id": sid, "title": stx, "text": stx, "level": 3, "children": []}
                        )

            return toc
        except Exception:
            return []

    @staticmethod
    def parse_sections_from_xml(
        xml_content: str, pmcid: str = ""
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        """Extract structured sections and references from PMC JATS XML.

        Args:
            xml_content: Complete JATS XML string for article.
            pmcid: Optional PubMed Central identifier for context.

        Returns:
            Tuple of extracted section records and reference dictionary.
        """
        try:
            root = ET.fromstring(JATSConverter.ensure_xmlns(xml_content).encode("utf-8"))
            sections = []
            references = {}  # Retained for API compatibility.

            # Counter for generating unique heading IDs.
            heading_counter = [0]

            def make_heading_id(text):
                """Generate a unique, URL-safe ID from heading text."""
                heading_counter[0] += 1
                slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50]
                return f"h-{heading_counter[0]}-{slug}"

            # Recursively render JATS XML nodes into semantic HTML.
            def get_html_recursive(node, is_root=False):
                parts = []

                # Skip figures without local media rendering.
                if node.tag == "fig":
                    return ""

                # Skip audio and video media elements.
                if node.tag == "media":
                    return ""

                # Skip supplementary materials.
                if node.tag == "supplementary-material":
                    return ""

                # Skip inline formula elements.
                if node.tag == "inline-formula":
                    return ""

                # Skip footnote elements.
                if node.tag in ("fn", "fn-group"):
                    return ""

                # Skip acknowledgments.
                if node.tag == "ack":
                    return ""

                # Skip bibliography reference lists.
                if node.tag == "ref-list":
                    return ""

                # Render nested section titles as h3 headings.
                if not is_root and node.tag == "title":
                    title_text = "".join(node.itertext()).strip()
                    hid = make_heading_id(title_text)
                    return f'<h3 id="{hid}" class="article-h3">{title_text}</h3>'

                if node.tag == "p":
                    inner = _collect_children_html(node)
                    return f"<p>{inner}</p>"

                # Render tabular data wrapped in scrollable containers.
                if node.tag == "table-wrap":
                    label = node.findtext("label") or "Table"
                    cap_node = node.find("caption")
                    caption = "".join(cap_node.itertext()) if cap_node is not None else ""
                    table_node = node.find(".//table")
                    if table_node is not None:
                        table_html = ET.tostring(table_node, encoding="unicode", method="html")
                        cap_html = f'<p class="table-caption">{caption}</p>' if caption else ""
                        return (
                            f'<div class="article-table-wrap">'
                            f'<span class="table-label">{label}</span>'
                            f'<div class="table-scroll-container jats-table">{table_html}</div>'
                            f"{cap_html}"
                            f"</div>"
                        )
                    return ""

                # Render figure captions while omitting remote images.
                if node.tag == "fig":
                    label = node.findtext("label") or "Figure"
                    cap_node = node.find("caption")
                    caption = "".join(cap_node.itertext()) if cap_node is not None else ""
                    if caption:
                        return f'<div class="article-figure"><span class="fig-label">{label}</span>: {caption}</div>'
                    return (
                        f'<div class="article-figure"><span class="fig-label">{label}</span></div>'
                    )

                # Format lists preserving original numbering styles.
                if node.tag == "list":
                    list_type = node.get("list-type", "")
                    # Detect explicit labels on list items.
                    first_li = node.find("list-item")
                    has_labels = first_li is not None and first_li.find("label") is not None

                    if has_labels:
                        # Render verbatim item labels.
                        items = []
                        for li_node in node.findall("list-item"):
                            label_el = li_node.find("label")
                            lbl = (
                                "".join(label_el.itertext()).strip() if label_el is not None else ""
                            )
                            inner_parts = []
                            for child in li_node:
                                if child.tag == "label":
                                    if child.tail:
                                        inner_parts.append(child.tail)
                                    continue
                                inner_parts.append(get_html_recursive(child, is_root=False))
                                if child.tail:
                                    inner_parts.append(child.tail)
                            li_inner = "".join(inner_parts)
                            items.append(
                                f'<li style="list-style:none !important"><span class="list-label">{lbl}</span> {li_inner}</li>'
                            )
                        return (
                            '<ol style="list-style:none !important;padding-left:1.5em">'
                            + "".join(items)
                            + "</ol>"
                        )
                    else:
                        # Map list types to semantic HTML elements.
                        OL_TYPE_MAP = {
                            "order": ("1", "decimal"),
                            "ordered": ("1", "decimal"),
                            "alpha-lower": ("a", "lower-alpha"),
                            "alpha-upper": ("A", "upper-alpha"),
                            "roman-lower": ("i", "lower-roman"),
                            "roman-upper": ("I", "upper-roman"),
                            "bullet": ("", "disc"),
                        }
                        mapped_info = OL_TYPE_MAP.get(list_type, ("", "disc"))
                        mapped_type, css_style = mapped_info

                        if mapped_type:
                            tag_open = f'<ol type="{mapped_type}" style="list-style-type: {css_style} !important;">'
                            tag_close = "</ol>"
                        else:
                            tag_open = f'<ul style="list-style-type: {css_style} !important;">'
                            tag_close = "</ul>"

                        items = []
                        for li_node in node.findall("list-item"):
                            li_inner = "".join(
                                get_html_recursive(child, is_root=False) for child in li_node
                            )
                            items.append(f"<li>{li_inner}</li>")
                        return tag_open + "".join(items) + tag_close

                if node.tag == "list-item":
                    inner = "".join(get_html_recursive(child, is_root=False) for child in node)
                    return f"<li>{inner}</li>"

                if node.tag == "disp-quote":
                    inner = _collect_children_html(node)
                    return f"<blockquote>{inner}</blockquote>"

                INLINE_FORMATTING = {
                    "italic": ("em", ""),
                    "bold": ("strong", ""),
                    "sub": ("sub", ""),
                    "sup": ("sup", ""),
                    "sc": ("span", ' class="small-caps"'),
                    "monospace": ("code", ""),
                    "underline": ("span", ' class="underline"'),
                }
                if node.tag in INLINE_FORMATTING:
                    html_tag, extra_attrs = INLINE_FORMATTING[node.tag]
                    inner = _collect_children_html(node)
                    return f"<{html_tag}{extra_attrs}>{inner}</{html_tag}>"

                if node.tag == "ext-link":
                    href = node.get("{http://www.w3.org/1999/xlink}href") or ""
                    inner = _collect_children_html(node)
                    if href:
                        return f'<a href="{href}" target="_blank" rel="noopener">{inner}</a>'
                    return inner

                if node.tag == "email":
                    addr = (node.text or "").strip()
                    return f'<a href="mailto:{addr}">{addr}</a>'

                if node.tag == "named-content":
                    return _collect_children_html(node)

                if node.tag == "sec":
                    inner_parts = []
                    # Extract section numbering label.
                    label_node = node.find("label")
                    label_text = (
                        "".join(label_node.itertext()).strip() if label_node is not None else ""
                    )
                    title_node = node.find("title")
                    title_text = (
                        "".join(title_node.itertext()).strip() if title_node is not None else ""
                    )

                    # Omit h3 when title duplicates parent heading.
                    parent = node.getparent()
                    is_duplicate = False
                    if parent is not None and parent.tag == "sec":
                        p_label = parent.find("label")
                        p_title = parent.find("title")

                        def normalize(t):
                            if not t:
                                return ""
                            return re.sub(r"[^a-z0-9]+", "", t.lower())

                        p_label_text = (
                            "".join(p_label.itertext()).strip() if p_label is not None else ""
                        )
                        p_title_text = (
                            "".join(p_title.itertext()).strip() if p_title is not None else ""
                        )

                        if normalize(label_text) == normalize(p_label_text) and normalize(
                            title_text
                        ) == normalize(p_title_text):
                            is_duplicate = True

                    for child in node:
                        if child.tag == "label" or child.tag == "title":
                            if is_root:
                                # Skip title and label rendered in h2.
                                if child.tail:
                                    inner_parts.append(child.tail)
                                continue
                            if child.tag == "label":
                                continue
                            if child.tag == "title":
                                if is_duplicate:
                                    continue
                                if label_text:
                                    full_title = f"{label_text} {title_text}"
                                else:
                                    full_title = title_text
                                hid = make_heading_id(full_title)
                                inner_parts.append(
                                    f'<h3 id="{hid}" class="article-h3">{full_title}</h3>'
                                )
                                if child.tail:
                                    inner_parts.append(child.tail)
                                continue

                        inner_parts.append(get_html_recursive(child, is_root=False))
                        if child.tail:
                            inner_parts.append(child.tail)
                    return "".join(inner_parts)

                return _collect_children_html(node, is_root=is_root)

            def _collect_children_html(node, is_root=False):
                """Collect node text, child HTML, and tails into a string."""
                parts = []
                if node.text:
                    parts.append(node.text)
                for child in node:
                    if is_root and child.tag in ("title", "label"):
                        # Skip title and label handled in heading.
                        if child.tail:
                            parts.append(child.tail)
                        continue
                    parts.append(get_html_recursive(child, is_root=False))
                    if child.tail:
                        parts.append(child.tail)
                return "".join(parts)

            def _extract_headings_from_html(html_content):
                """Extract heading metadata from HTML tags and markers."""
                headings = []
                # Parse heading tags generated from section titles.
                for m in re.finditer(
                    r'<h3[^>]*id="([^"]*)"[^>]*class="[^"]*article-h3[^"]*"[^>]*>(.*?)</h3>',
                    html_content,
                    re.S,
                ):
                    headings.append({"id": m.group(1), "text": m.group(2).strip()})
                # Parse preserved bracketed heading markers.
                counter = [0]
                for m in re.finditer(
                    r"\[H3\](.*?)\[/H3\]",
                    html_content,
                    re.S,
                ):
                    counter[0] += 1
                    text = m.group(1).strip()
                    slug = re.sub(r"[^a-z0-9]+", "-", text.lower())[:50]
                    headings.append({"id": slug, "text": text})
                return headings

            # Extract abstract section content.
            abstract_node = root.find(".//abstract")
            if abstract_node is not None:
                abs_html = get_html_recursive(abstract_node, is_root=True).strip()
                if abs_html:
                    headings = _extract_headings_from_html(abs_html)
                    sections.append(
                        {
                            "title": "Abstract",
                            "content": abs_html,
                            "headings": headings,
                        }
                    )

            # Extract document body sections.
            body = root.find(".//body")
            if body is not None:
                for sec in body.findall("./sec"):
                    # Combine section label and title text.
                    label_node = sec.find("label")
                    title_node = sec.find("title")
                    label_text = (
                        label_node.text.strip()
                        if label_node is not None and label_node.text
                        else ""
                    )
                    title = "Section"
                    if title_node is not None:
                        title = "".join(title_node.itertext()).strip()
                    if label_text:
                        title = f"{label_text} {title}"
                    content = get_html_recursive(sec, is_root=True).strip()
                    if content:
                        headings = _extract_headings_from_html(content)
                        sections.append({"title": title, "content": content, "headings": headings})

            # Extract body sections only; back matter is excluded.
            return sections, references
        except Exception as e:
            logger.error(f"Error parsing XML sections: {e}")
            return [], {}
