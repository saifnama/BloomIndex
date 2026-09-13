import logging
import re
import time
from typing import Any

from backend.src.common.http_client import HttpClientManager
from backend.src.papers.europe_pmc import EuropePMCService

logger = logging.getLogger(__name__)

# In-memory search result cache.
_SEARCH_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_TTL = 300  # 5 minutes


class SearchService:
    OPENALEX_BASE_URL = "https://api.openalex.org"

    @staticmethod
    def _map_openalex_type(article_type: str) -> str:
        """Normalize article type identifier for OpenAlex queries.

        Converts Europe PMC naming conventions (such as research-article
        to article) and accepts native OpenAlex categories directly.
        """
        normalized = (article_type or "").strip().lower()
        epmc_to_oa = {
            "research-article": "article",
            "review": "review",
        }
        return epmc_to_oa.get(normalized, normalized)

    @classmethod
    async def get_openalex_type_counts(cls) -> list[dict[str, Any]]:
        """Fetch work type counts from OpenAlex API.

        Returns key, display_name, and count tuples sorted by frequency.
        Cached for one hour.
        """
        cache_key = "openalex_type_counts"
        now = time.time()
        if cache_key in _SEARCH_CACHE:
            cached = _SEARCH_CACHE[cache_key]
            if cached.get("timestamp", 0) + 3600 > now:
                return cached.get("data", [])

        try:
            client = await HttpClientManager.get_client()
            # Align filters with search_openalex baseline constraints.
            response = await client.get(
                f"{cls.OPENALEX_BASE_URL}/works",
                params={
                    "group_by": "type",
                    "per_page": 200,
                    "filter": "has_doi:true,has_content.pdf:true,is_oa:true,primary_topic.domain.id:1",
                },
                timeout=15.0,
            )
            response.raise_for_status()
            data = response.json()

            results = []
            for group in data.get("group_by", []):
                key = group.get("key_display_name", "")
                count = group.get("count", 0)
                if key:
                    results.append(
                        {
                            "key": key,
                            "display_name": key.replace("-", " ").title(),
                            "count": count,
                        }
                    )

            # Sort by count descending
            results.sort(key=lambda x: x["count"], reverse=True)

            _SEARCH_CACHE[cache_key] = {"data": results, "timestamp": now}
            return results
        except Exception as e:
            logger.warning(f"Failed to fetch OpenAlex type counts: {e}")
            # Return fallback list with no hardcoded counts
            return [
                {"key": "article", "display_name": "Article", "count": None},
                {"key": "book-chapter", "display_name": "Book Chapter", "count": None},
                {"key": "dataset", "display_name": "Dataset", "count": None},
                {"key": "other", "display_name": "Other", "count": None},
                {"key": "dissertation", "display_name": "Dissertation", "count": None},
                {"key": "preprint", "display_name": "Preprint", "count": None},
                {"key": "book", "display_name": "Book", "count": None},
                {"key": "review", "display_name": "Review", "count": None},
                {"key": "paratext", "display_name": "Paratext", "count": None},
                {"key": "libguides", "display_name": "Libguides", "count": None},
                {"key": "letter", "display_name": "Letter", "count": None},
                {"key": "report", "display_name": "Report", "count": None},
                {"key": "peer-review", "display_name": "Peer Review", "count": None},
                {"key": "reference-entry", "display_name": "Reference Entry", "count": None},
                {"key": "editorial", "display_name": "Editorial", "count": None},
                {"key": "erratum", "display_name": "Erratum", "count": None},
                {"key": "standard", "display_name": "Standard", "count": None},
                {
                    "key": "supplementary-materials",
                    "display_name": "Supplementary Materials",
                    "count": None,
                },
                {"key": "retraction", "display_name": "Retraction", "count": None},
                {"key": "software", "display_name": "Software", "count": None},
                {"key": "database", "display_name": "Database", "count": None},
                {"key": "book-section", "display_name": "Book Section", "count": None},
                {"key": "report-component", "display_name": "Report Component", "count": None},
                {"key": "grant", "display_name": "Grant", "count": None},
            ]

    @staticmethod
    def _normalize_doi(value: str | None) -> str:
        if not value:
            return ""
        normalized = value.strip().lower()
        normalized = re.sub(r"^https?://(dx\.)?doi\.org/", "", normalized)
        normalized = re.sub(r"^doi:", "", normalized)
        return normalized.strip()

    @staticmethod
    def _normalize_pmid(value: str | None) -> str:
        if not value:
            return ""
        digits = re.findall(r"\d+", value)
        return digits[0] if digits else ""

    @staticmethod
    def _normalize_pmcid(value: str | None) -> str:
        if not value:
            return ""
        match = re.search(r"PMC\d+", value.upper())
        return match.group(0) if match else ""

    @classmethod
    def _build_dedupe_key(cls, item: dict[str, Any]) -> str:
        doi = cls._normalize_doi(item.get("doi"))
        if doi:
            return f"doi:{doi}"

        pmid = cls._normalize_pmid(item.get("pmid"))
        if pmid:
            return f"pmid:{pmid}"

        pmcid = cls._normalize_pmcid(item.get("pmcid"))
        if pmcid:
            return f"pmcid:{pmcid}"

        title = re.sub(r"\s+", " ", (item.get("title") or "").strip().lower())
        year = str(item.get("year") or "")
        return f"title:{title}|year:{year}"

    @staticmethod
    def _coerce_year(value: Any) -> str:
        if value is None:
            return "Unknown year"
        text = str(value).strip()
        return text or "Unknown year"

    @staticmethod
    def _join_authors(authorships: list[dict[str, Any]]) -> str:
        names = [
            authorship.get("author", {}).get("display_name", "").strip()
            for authorship in authorships or []
            if authorship.get("author", {}).get("display_name", "").strip()
        ]
        return ", ".join(names[:10]) if names else "Unknown authors"

    @staticmethod
    def _reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str:
        if not inverted_index:
            return ""

        # Each position maps to exactly one word (first occurrence)
        position_to_word: dict[int, str] = {}
        for word, positions in inverted_index.items():
            for pos in positions:
                if pos not in position_to_word:
                    position_to_word[pos] = word

        if not position_to_word:
            return ""

        max_position = max(position_to_word.keys())
        words = [position_to_word.get(i, "") for i in range(max_position + 1)]
        return " ".join(words).strip()

    @classmethod
    def _format_openalex_work(cls, work: dict[str, Any]) -> dict[str, Any]:
        """Normalize an OpenAlex work dictionary to search result schema.

        Extracts identifiers, primary source metadata, and flags open
        access and full-text availability.
        """
        ids = work.get("ids", {}) or {}
        doi = cls._normalize_doi(ids.get("doi") or work.get("doi"))
        pmid = cls._normalize_pmid(ids.get("pmid") or work.get("pmid"))
        pmcid = cls._normalize_pmcid(ids.get("pmcid") or work.get("pmcid"))
        primary_location = work.get("primary_location", {}) or {}
        source = primary_location.get("source", {}) or {}

        # Prefer best open access location before secondary locations.
        best_oa = work.get("best_oa_location") or {}
        pdf_url = best_oa.get("pdf_url") or next(
            (loc.get("pdf_url") for loc in work.get("locations", []) or [] if loc.get("pdf_url")),
            None,
        )

        return {
            "id": work.get("id")
            or f"openalex:{doi or pmid or pmcid or work.get('display_name') or work.get('title', '')}",
            "pmcid": pmcid or None,
            "doi": doi or None,
            "pmid": pmid or None,
            "title": work.get("display_name") or work.get("title") or "No title available",
            "authors": cls._join_authors(work.get("authorships", [])),
            "journal": source.get("display_name") or "Unknown journal",
            "year": cls._coerce_year(work.get("publication_year")),
            "citationCount": work.get("cited_by_count", 0),
            "isOpenAccess": bool((work.get("open_access") or {}).get("is_oa")),
            "hasTextMinedTerms": False,
            "hasFullText": bool(pdf_url) or bool(work.get("has_fulltext")),
            "hasPdfUrl": bool(pdf_url),
            "pdfUrl": pdf_url,
            "abstract": cls._reconstruct_abstract(work.get("abstract_inverted_index")),
            "source": "OpenAlex",
        }

    @classmethod
    async def fetch_openalex_by_doi(cls, doi: str) -> dict[str, Any] | None:
        """Fetch article metadata for an exact DOI from OpenAlex.

        Direct identifier lookup without topical or quality filters.
        """
        doi = (doi or "").strip().lower()
        if not doi:
            return None
        try:
            client = await HttpClientManager.get_client()
            response = await client.get(f"{cls.OPENALEX_BASE_URL}/works/doi:{doi}", timeout=30.0)
            if response.status_code != 200:
                return None
            return cls._format_openalex_work(response.json())
        except Exception as e:
            logger.warning(f"OpenAlex DOI lookup failed for {doi}: {e}")
            return None

    @classmethod
    async def search_openalex(
        cls,
        query: str,
        filters: dict[str, Any],
        max_results: int,
        page: int,
        sort: str = "",
    ) -> dict[str, Any]:
        if not query.strip():
            return {
                "results": [],
                "pagination": {"total": 0, "page": page, "pageSize": max_results, "hasMore": False},
            }

        params: dict[str, Any] = {
            "search": query.strip(),
            "per_page": max_results,
            "page": max(1, page),
        }

        filter_parts: list[str] = []

        # Always apply these filters for quality papers
        filter_parts.append("has_doi:true")  # Must have DOI
        filter_parts.append("has_content.pdf:true")  # Must have PDF
        filter_parts.append("is_oa:true")  # Must be Open Access
        filter_parts.append("primary_topic.domain.id:1")  # Biology/Life Sciences

        if filters.get("article_type"):
            filter_parts.append(f"type:{cls._map_openalex_type(filters['article_type'])}")

        if filter_parts:
            params["filter"] = ",".join(filter_parts)

        if sort == "cited":
            params["sort"] = "cited_by_count:desc"
        elif sort == "date":
            params["sort"] = "publication_date:desc"
        elif sort == "date_asc":
            params["sort"] = "publication_date:asc"

        client = await HttpClientManager.get_client()
        response = await client.get(f"{cls.OPENALEX_BASE_URL}/works", params=params, timeout=30.0)
        response.raise_for_status()
        data = response.json()

        results = [cls._format_openalex_work(work) for work in data.get("results", [])]

        meta = data.get("meta", {}) or {}
        total = int(meta.get("count", 0) or 0)
        has_more = page * max_results < total
        return {
            "results": results,
            "pagination": {
                "total": total,
                "page": page,
                "pageSize": max_results,
                "hasMore": has_more,
            },
        }

    @classmethod
    async def search_literature(
        cls,
        query: str,
        filters: dict[str, Any],
        page_size: int = 25,
        page: int = 1,
        sort: str = "",
        source: str = "europepmc",  # europepmc or openalex.
    ) -> dict[str, Any]:
        """Search literature across Europe PMC and OpenAlex.

        Keyword queries respect requested provider sources. Identifier
        queries bypass source selectors to resolve matches across all
        providers.
        """
        if not query.strip():
            return {
                "results": [],
                "pagination": {"total": 0, "page": 1, "pageSize": page_size, "hasMore": False},
            }

        fetch_size = min(100, max(page_size, page * page_size * 2))

        # Check if query matches a scholarly identifier pattern.
        id_type, id_value = EuropePMCService.parse_identifier(query)

        is_identifier = id_type in ("doi", "pmcid", "pmid")

        if is_identifier:
            # Query identifier across all supported provider sources.
            return await cls.search_by_identifier(id_type, id_value, page_size, page)

        # Include sort and filters in cache key to avoid stale ordering.
        filter_sig = ",".join(f"{k}={filters.get(k) or ''}" for k in sorted(filters))
        cache_key = f"{source}:{query}:{page}:{page_size}:{sort}:{filter_sig}"
        if cache_key in _SEARCH_CACHE:
            cached = _SEARCH_CACHE[cache_key]
            if cached.get("timestamp", 0) + _CACHE_TTL > time.time():
                return cached.get("data")

        # Keyword search dispatched to configured provider.
        results: list[dict[str, Any]] = []
        total = 0
        has_more = False

        if source == "openalex":
            openalex_data = await cls.search_openalex(
                query=query,
                filters=filters,
                max_results=fetch_size,
                page=1,
                sort=sort,
            )
            results = openalex_data.get("results", []) if openalex_data else []
            total = openalex_data.get("pagination", {}).get("total", 0) if openalex_data else 0
            has_more = (
                openalex_data.get("pagination", {}).get("hasMore", False)
                if openalex_data
                else False
            )
        else:
            europe_data = await EuropePMCService.search_literature(
                query=query,
                filters=filters,
                max_results=fetch_size,
                sort=sort,
                cursor_mark="*",
            )
            results = europe_data.get("results", []) if europe_data else []
            total = europe_data.get("pagination", {}).get("total", 0) if europe_data else 0
            has_more = (
                europe_data.get("pagination", {}).get("hasMore", False) if europe_data else False
            )

        start = max(0, (page - 1) * page_size)
        end = start + page_size

        if filters.get("has_full_text"):
            results = cls._filter_full_text(results)

        page_results = results[start:end]

        return {
            "results": page_results,
            "pagination": {
                "total": total,
                "page": page,
                "pageSize": page_size,
                "hasMore": has_more and len(results) > end,
            },
        }

    @classmethod
    def _filter_full_text(cls, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Filter results that have full-text available."""
        return [
            r for r in results if r.get("hasFullText") or r.get("isOpenAccess") or r.get("pmcid")
        ]

    @classmethod
    async def search_by_identifier(
        cls,
        id_type: str,
        id_value: str,
        page_size: int = 25,
        page: int = 1,
    ) -> dict[str, Any]:
        """Search across all providers by identifier (DOI, PMCID, or PMID).

        Evaluates providers in sequence, returning full-text results
        immediately or falling back to abstract and metadata records.
        """
        from backend.src.papers.resolver import (
            fetch_from_semantic_scholar,
            fetch_pmc_by_pmcid,
        )

        # Content score: 2 = full-text, 1 = abstract, 0 = metadata only.
        def _content_score(item: dict[str, Any]) -> int:
            if (
                item.get("hasFullText")
                or item.get("pdfUrl")
                or item.get("openAccessPdf")
                or item.get("full_text_xml")
            ):
                return 2
            abstract = (item.get("abstract") or "").strip()
            if abstract:
                return 1
            return 0

        best_candidate = None
        best_score = -1

        # Query Europe PMC via exact identifier lookup.
        first = await EuropePMCService.fetch_by_identifier(id_type, id_value)
        if first:
            first["source"] = "Europe PMC"
            score = _content_score(first)
            if score > best_score:
                best_candidate = first
                best_score = score
            if score == 2:  # Full text available; return immediately.
                return {
                    "results": [first],
                    "pagination": {"total": 1, "page": 1, "pageSize": 1, "hasMore": False},
                }
            # Non-DOI identifiers return Europe PMC record directly.
            if id_type != "doi":
                return {
                    "results": [first],
                    "pagination": {"total": 1, "page": 1, "pageSize": 1, "hasMore": False},
                }

        # Query OpenAlex via exact DOI identifier lookup.
        if id_type == "doi":
            first = await cls.fetch_openalex_by_doi(id_value)
            if first:
                score = _content_score(first)
                if score > best_score:
                    best_candidate = first
                    best_score = score
                if score == 2:  # PDF available; return immediately.
                    return {
                        "results": [first],
                        "pagination": {"total": 1, "page": 1, "pageSize": 1, "hasMore": False},
                    }
                # Continue checking downstream providers for full text.

            # Try Semantic Scholar fallback (direct DOI fetch)
            ss_data = await fetch_from_semantic_scholar(id_value)
            if ss_data:
                # Build result dict matching expected fields
                ss_result = {
                    "doi": ss_data.get("doi", id_value),
                    "title": ss_data.get("title", ""),
                    "authors": ss_data.get("authors", []),
                    "year": ss_data.get("year"),
                    "journal": ss_data.get("journal", ""),
                    "pmcid": ss_data.get("pmcid", ""),
                    "pmid": ss_data.get("pmid", ""),
                    "abstract": ss_data.get("abstract", ""),
                    "source": ss_data.get("source", "Semantic Scholar"),
                    "openAccessPdf": ss_data.get("openAccessPdf"),
                    "isOpenAccess": bool(ss_data.get("isOpenAccess")),
                }
                score = _content_score(ss_result)
                if score > best_score:
                    best_candidate = ss_result
                    best_score = score
                if score == 2:
                    return {
                        "results": [ss_result],
                        "pagination": {"total": 1, "page": 1, "pageSize": 1, "hasMore": False},
                    }

        # PMCID fallback (PMC direct)
        if id_type == "pmcid":
            pmc_data = await fetch_pmc_by_pmcid(id_value)
            if pmc_data:
                pmc_result = {
                    "pmcid": pmc_data.get("pmcid", id_value),
                    "title": pmc_data.get("title", ""),
                    "authors": pmc_data.get("authors", []),
                    "year": pmc_data.get("year"),
                    "journal": pmc_data.get("journal", ""),
                    "abstract": pmc_data.get("abstract", ""),
                    "source": "PMC",
                    "full_text_xml": pmc_data.get("full_text_xml"),
                }
                score = _content_score(pmc_result)
                if score > best_score:
                    best_candidate = pmc_result
                    best_score = score
                if score == 2:
                    return {
                        "results": [pmc_result],
                        "pagination": {"total": 1, "page": 1, "pageSize": 1, "hasMore": False},
                    }

        # Return best candidate found (may be metadata-only)
        if best_candidate:
            return {
                "results": [best_candidate],
                "pagination": {"total": 1, "page": 1, "pageSize": 1, "hasMore": False},
            }

        # Nothing found anywhere
        return {
            "results": [],
            "pagination": {"total": 0, "page": 1, "pageSize": page_size, "hasMore": False},
        }
