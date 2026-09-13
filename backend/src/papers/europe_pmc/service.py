"""Europe PMC service facade.

Unifies HTTP client requests and JATS XML parsing, managing paper data
fetching, structured content extraction, and versioned render caching.
"""

import hashlib
import logging
import os
from typing import Any

from backend.src.common.caching import pmc_cache
from backend.src.common.sanitizer import sanitize
from backend.src.papers.europe_pmc.client import EuropePMCClient
from backend.src.papers.europe_pmc.jats import JATSConverter, XMLParser

logger = logging.getLogger(__name__)

# Invalidate rendered HTML cache entries when parser logic changes.
try:
    _this_file = os.path.abspath(__file__)
    with open(_this_file, "rb") as _f:
        PARSER_VERSION = hashlib.md5(_f.read()).hexdigest()[:8]
except Exception:
    PARSER_VERSION = "dev"


def _render_cache_key_for(identifier_value: str) -> str:
    """Return version-namespaced cache key for rendered HTML."""
    return f"rendered|{identifier_value}|ver={PARSER_VERSION}"


class EuropePMCService:
    """Facade orchestrating Europe PMC API requests and JATS parsing."""

    BASE_URL = EuropePMCClient.BASE_URL

    # HTTP client delegate methods.

    @staticmethod
    def parse_identifier(raw_input: str) -> tuple[str, str]:
        return EuropePMCClient.parse_identifier(raw_input)

    @classmethod
    async def fetch_full_text(cls, pmcid: str) -> str | None:
        return await EuropePMCClient.fetch_full_text(pmcid)

    @classmethod
    async def fetch_paper_data(cls, doi: str) -> tuple[str | None, str]:
        return await EuropePMCClient.fetch_paper_data(doi)

    @classmethod
    async def resolve_pdf_url(cls, identifier: str) -> dict[str, str] | None:
        return await EuropePMCClient.resolve_pdf_url(identifier)

    @classmethod
    async def search_literature(
        cls,
        query: str,
        filters: dict,
        max_results: int = 25,
        sort: str = "",
        cursor_mark: str = "*",
    ) -> dict[str, Any]:
        return await EuropePMCClient.search_literature(
            query, filters, max_results, sort, cursor_mark
        )

    @classmethod
    async def fetch_by_identifier(cls, id_type: str, id_value: str) -> dict[str, Any] | None:
        """Lookup paper record by exact DOI, PMCID, or PMID."""
        return await EuropePMCClient.fetch_by_identifier(id_type, id_value)

    # XML parser delegate methods.

    @staticmethod
    def extract_title_from_xml(xml_content: str) -> str:
        return XMLParser.extract_title_from_xml(xml_content)

    @staticmethod
    def extract_authors_from_xml(xml_content: str) -> list:
        return XMLParser.extract_authors_from_xml(xml_content)

    @staticmethod
    def extract_journal_from_xml(xml_content: str) -> str:
        return XMLParser.extract_journal_from_xml(xml_content)

    @staticmethod
    def extract_date_from_xml(xml_content: str) -> str:
        return XMLParser.extract_date_from_xml(xml_content)

    @staticmethod
    def extract_toc_from_html(html_content: str) -> list[dict[str, Any]]:
        return XMLParser.extract_toc_from_html(html_content)

    @staticmethod
    def parse_sections_from_xml(
        xml_content: str, pmcid: str = ""
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        return XMLParser.parse_sections_from_xml(xml_content, pmcid)

    # JATS converter delegate methods.

    @staticmethod
    def _jats_inline_to_html(text: str) -> str:
        return JATSConverter.inline_to_html(text)

    @staticmethod
    def _ensure_xmlns(xml_content: str) -> str:
        return JATSConverter.ensure_xmlns(xml_content)

    @staticmethod
    def clean_xml(xml_content: str) -> str:
        return JATSConverter.clean_xml(xml_content)

    # Service orchestration methods.

    @classmethod
    async def fetch_structured_data(cls, doi: str) -> dict[str, Any]:
        """Fetch paper content with structured sections and table of contents.

        Retrieves paper XML, extracts sections and metadata, renders
        semantic HTML, and caches parsed representations.

        Args:
            doi: Bibliographic identifier string or URL.

        Returns:
            Dictionary with sections, full HTML, TOC, and metadata.
        """
        # Return pre-rendered HTML cache to avoid re-parsing XML.
        id_type, id_value = cls.parse_identifier(doi)
        render_key = _render_cache_key_for(id_value)
        cached_render = pmc_cache.get(render_key)
        if cached_render:
            cached_html = cached_render.get("html", "")
            # Invalidate cached payload if section headings are missing.
            if cached_html and ("<h2" in cached_html or "<H2" in cached_html):
                return {
                    "sections": cached_render.get("sections", []),
                    "html": cached_render.get("html", ""),
                    "mode": cached_render.get("mode", ""),
                    "title": cached_render.get("title", ""),
                    "references": cached_render.get("references", {}),
                    "pmcid": cached_render.get("pmcid", ""),
                    "toc": cached_render.get("toc", []),
                    "journal": cached_render.get("journal", ""),
                    "authors": cached_render.get("authors", []),
                    "doi": cached_render.get("doi", ""),
                    "date": cached_render.get("date", ""),
                    "isOpenAccess": bool(cached_render.get("isOpenAccess")),
                    "fallback_source": "Europe PMC",
                    "fallback_url": f"https://europepmc.org/article/{id_value}",
                }

        # Fetch paper payload from remote endpoints when cache misses.
        id_type, id_value = cls.parse_identifier(doi)
        text, mode = await cls.fetch_paper_data(doi)

        # Read metadata populated during primary paper retrieval.
        cached_paper = pmc_cache.get(id_value) or {}
        paper_title_from_cache = cached_paper.get("title", "")
        paper_journal_from_cache = cached_paper.get("journal", "")
        paper_authors_from_cache = cached_paper.get("authors", [])
        paper_doi_from_cache = cached_paper.get("doi", "")
        paper_date_from_cache = cached_paper.get("date", "")
        paper_is_oa = bool(cached_paper.get("is_oa"))

        if mode == "error" or not text:
            return {
                "sections": [],
                "html": "",
                "mode": mode,
                "title": paper_title_from_cache,
                "references": {},
                "pmcid": id_value if id_type == "pmcid" else "",
                "toc": [],
                "journal": paper_journal_from_cache,
                "authors": paper_authors_from_cache,
                "doi": paper_doi_from_cache,
                "date": paper_date_from_cache,
                "isOpenAccess": paper_is_oa,
                "fallback_source": "",
            }

        # Retain PMCID identifier for asset link resolution.
        pmcid = id_value if id_type == "pmcid" else ""

        if mode == "abstract":
            # Convert JATS inline formatting prior to sanitization.
            abstract_html = cls._jats_inline_to_html(text)
            abstract_html = sanitize(abstract_html)
            # Extract table of contents from rendered abstract HTML.
            toc = cls.extract_toc_from_html(abstract_html)
            # Extract title and metadata attributes from JATS XML.
            paper_title = ""
            if text.startswith("<?xml"):
                metadata = XMLParser.extract_metadata_from_xml(text)
                paper_title = metadata["title"]
                if not paper_journal_from_cache:
                    paper_journal_from_cache = metadata["journal"]
                if not paper_authors_from_cache:
                    paper_authors_from_cache = metadata["authors"]
                if not paper_date_from_cache:
                    paper_date_from_cache = metadata["date"]
            return {
                "sections": [{"title": "Abstract", "content": abstract_html, "headings": []}],
                "html": abstract_html,
                "mode": mode,
                "title": paper_title or paper_title_from_cache,
                "references": {},
                "pmcid": pmcid,
                "toc": toc,
                "journal": paper_journal_from_cache,
                "authors": paper_authors_from_cache,
                "doi": paper_doi_from_cache,
                "date": paper_date_from_cache,
                "isOpenAccess": paper_is_oa,
                "fallback_source": "Europe PMC",
            }

        # Extract PMCID from XML attributes when query lacks PMCID.
        if not pmcid:
            try:
                from lxml import etree as ET

                root = ET.fromstring(cls._ensure_xmlns(text).encode("utf-8"))
                pmc_node = root.find(".//article-id[@pub-id-type='pmc']")
                if pmc_node is not None:
                    pmcid = pmc_node.text
                    if not pmcid.startswith("PMC"):
                        pmcid = f"PMC{pmcid}"
            except Exception:
                pass

        sections, references = cls.parse_sections_from_xml(text, pmcid=pmcid)

        # Extract metadata attributes in a single XML parsing traversal.
        paper_title = ""
        if text.startswith("<?xml"):
            metadata = XMLParser.extract_metadata_from_xml(text)
            paper_title = metadata["title"]
            if not paper_journal_from_cache:
                paper_journal_from_cache = metadata["journal"]
            if not paper_authors_from_cache:
                paper_authors_from_cache = metadata["authors"]
            if not paper_date_from_cache:
                paper_date_from_cache = metadata["date"]
        if not paper_title:
            paper_title = paper_title_from_cache

        if not sections:
            return {
                # Retain sections array for backward compatibility.
                "sections": [{"title": "Full Text", "content": text, "headings": []}],
                "html": text,
                "mode": mode,
                "title": paper_title,
                "references": references,
                "pmcid": pmcid,
                "toc": [],
                "journal": paper_journal_from_cache,
                "authors": paper_authors_from_cache,
                "doi": paper_doi_from_cache,
                "date": paper_date_from_cache,
                "isOpenAccess": paper_is_oa,
                "fallback_source": "Europe PMC",
            }

        # Sanitize HTML contents across all extracted sections.
        for s in sections:
            if isinstance(s, dict) and "content" in s:
                s["content"] = sanitize(s["content"])

        # Wrap sections in semantic HTML elements with h2 headings.
        rendered_sections = []
        for i, s in enumerate(sections):
            sec_title = s.get("title", "").strip()
            sec_content = s.get("content", "")
            sec_id = f"section-{i}"
            heading_html = (
                f'<h2 id="{sec_id}" class="article-h2">{sec_title}</h2>' if sec_title else ""
            )
            rendered_sections.append(
                f'<section id="{sec_id}">{heading_html}{sec_content}</section>'
            )
        full_html = "".join(rendered_sections)
        toc = cls.extract_toc_from_html(full_html)
        # Store rendered HTML and table of contents in cache.
        try:
            cache_payload = {
                "sections": sections,
                "html": full_html,
                "mode": mode,
                "title": paper_title,
                "references": references,
                "pmcid": pmcid,
                "toc": toc,
                "journal": paper_journal_from_cache,
                "authors": paper_authors_from_cache,
                "doi": paper_doi_from_cache,
                "date": paper_date_from_cache,
                "isOpenAccess": paper_is_oa,
            }
            pmc_cache.set(render_key, cache_payload)
        except Exception:
            pass

        return {
            # Retain sections array for backward compatibility.
            "sections": sections,
            "html": full_html,
            "mode": mode,
            "title": paper_title,
            "references": references,
            "pmcid": pmcid,
            "toc": toc,
            "journal": paper_journal_from_cache,
            "authors": paper_authors_from_cache,
            "doi": paper_doi_from_cache,
            "date": paper_date_from_cache,
            "isOpenAccess": paper_is_oa,
            "fallback_source": "Europe PMC",
        }
