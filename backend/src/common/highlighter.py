"""HTML text highlighting utility for named entities.

Wraps recognized entity occurrences inside styled <span> tags without
corrupting HTML structure or touching non-text nodes.
"""

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


class Highlighter:
    """Entity highlighter operating safely on parsed HTML text nodes."""

    # Entity type to CSS class mapping.
    COLOR_MAP = {
        "CHEMICAL": "ent-chemical",
        "BIOACTIVITY": "ent-bioactivity",
        "LOCATION": "ent-location",
        "SPECIES": "ent-species",
        "PLANT PART": "ent-plant-part",
        "EXTRACTION METHOD": "ent-extraction-method",
        "DEVELOPMENT STAGE": "ent-development-stage",
        "SEASON": "ent-season",
        "ANALYTICAL TECHNIQUE": "ent-analytical-technique",
        "ISOLATION METHOD": "ent-isolation-method",
        "DISEASE": "ent-disease",
    }

    @classmethod
    def highlight(cls, html_content: str, entities: list[dict[str, Any]]) -> str:
        """Highlight entity matches in HTML text nodes.

        Parses the document with BeautifulSoup to preserve tag structure
        and prevent malformed markup.
        """
        if not html_content or not entities:
            return html_content

        try:
            from bs4 import BeautifulSoup, NavigableString

            soup = BeautifulSoup(html_content, "html.parser")

            # Unwrap presentational tags that artificially fragment
            # entities across text nodes. <sub>/<sup> are deliberately
            # kept: chemical formulas rely on them (e.g. H<sub>2</sub>O).
            for tag in soup.find_all(("em", "strong", "i", "b")):
                tag.unwrap()

            # Deduplicate entities and sort longest-first to prioritize
            # specific multi-word matches over shorter substrings.
            unique_entities = sorted(
                {(e["text"].lower(), e["label"]) for e in entities if e.get("text")},
                key=lambda x: len(x[0]),
                reverse=True,
            )
            if not unique_entities:
                return html_content

            full_pattern_str = "|".join([rf"\b{re.escape(e[0])}\b" for e in unique_entities])
            full_pattern = re.compile(f"({full_pattern_str})", re.IGNORECASE)

            # Traverse and highlight matching text nodes.
            for text_node in soup.find_all(string=True):
                if not isinstance(text_node, NavigableString) or not text_node.strip():
                    continue

                # Always skip verbatim and presentation contexts.
                parent_name = text_node.parent.name
                if parent_name in ("code", "script", "style"):
                    continue
                # Skip spans already annotated by a previous pass.
                if parent_name == "span":
                    p_classes = text_node.parent.get("class", [])
                    if any(c.startswith("ent-") for c in p_classes):
                        continue

                original_node_text = str(text_node)
                matches = list(full_pattern.finditer(original_node_text))
                if not matches:
                    continue

                new_content = []
                last_end = 0

                for m in matches:
                    start, end = m.start(), m.end()
                    found_text = m.group(0)

                    # Determine matched entity label and metadata.
                    match_label = "ENTITY"
                    match_entity = None
                    for t, l in unique_entities:
                        if t == found_text.lower():
                            match_label = l
                            break

                    for e in entities:
                        if (
                            e.get("text", "").lower() == found_text.lower()
                            and e.get("label") == match_label
                        ):
                            match_entity = e
                            break

                    css_class = cls.COLOR_MAP.get(match_label, "bg-gray-200")

                    # Append preceding unhighlighted text slice.
                    if start > last_end:
                        new_content.append(NavigableString(original_node_text[last_end:start]))

                    # Construct replacement span tag with styles.
                    span = soup.new_tag("span")
                    span["class"] = f"{css_class} rounded-sm transition-all hover:brightness-95"

                    # Attach taxonomy metadata when annotating species.
                    if match_label == "SPECIES" and match_entity:
                        if match_entity.get("accepted_scientific_name"):
                            span["data-accepted-scientific-name"] = match_entity[
                                "accepted_scientific_name"
                            ]
                        if match_entity.get("scientific_name_verified"):
                            span["data-scientific-name-verified"] = match_entity[
                                "scientific_name_verified"
                            ]
                        if match_entity.get("common_name"):
                            span["data-common-name"] = match_entity["common_name"]
                        if match_entity.get("name_type"):
                            span["data-name-type"] = match_entity["name_type"]
                        if match_entity.get("taxon_id"):
                            span["data-taxon-id"] = str(match_entity["taxon_id"])
                        if match_entity.get("source_db"):
                            span["data-source-db"] = match_entity["source_db"]
                        if match_entity.get("source_url"):
                            span["data-source-url"] = match_entity["source_url"]

                    span.string = found_text
                    new_content.append(span)
                    last_end = end

                # Append remaining text following the final match.
                if last_end < len(original_node_text):
                    new_content.append(NavigableString(original_node_text[last_end:]))

                text_node.replace_with(*new_content)

            return str(soup)
        except Exception as e:
            logger.warning(f"Highlighter error: {e}")
            return html_content
