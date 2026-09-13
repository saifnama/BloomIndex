"""Europe PMC HTTP API client.

Provides network communication with the Europe PMC REST API, including
identifier normalization, metadata searches, full-text JATS XML
retrieval, and PDF link resolution.
"""

import asyncio
import logging
import re
from datetime import datetime
from typing import Any
from xml.etree import ElementTree

import httpx

from backend.src.common.caching import pmc_cache
from backend.src.common.http_client import HttpClientManager

logger = logging.getLogger(__name__)


class EuropePMCClient:
    """Client for Europe PMC literature search and full-text retrieval."""

    BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest"

    @staticmethod
    def _build_search_query(id_type: str, id_value: str) -> str:
        if id_type == "pmcid":
            return f"PMCID:{id_value}"
        if id_type == "pmid":
            return f"EXT_ID:{id_value}"
        return f"DOI:{id_value}"

    @classmethod
    def _format_search_result(cls, r: dict[str, Any]) -> dict[str, Any]:
        """Normalize a Europe PMC search record into application schema.

        Maps API response flags to standard availability indicators:
        isOpenAccess reflects journal license, hasFullText verifies text
        availability via inEPMC, and hasPdfUrl indicates PDF links.
        """
        journal = r.get("journalTitle", "")
        if not journal:
            journal_info = r.get("journalInfo", {})
            if journal_info:
                journal = journal_info.get("journal", {}).get("title", "")
        if not journal:
            journal = "Unknown journal"

        in_epmc = r.get("inEPMC") == "Y"
        has_pdf = r.get("hasPDF") == "Y"
        has_full_text = in_epmc or r.get("hasFullText") == "Y"

        return {
            "id": r.get("id"),
            "pmcid": r.get("pmcid"),
            "doi": r.get("doi"),
            "pmid": r.get("pmid"),
            "title": r.get("title", "No title available"),
            "authors": r.get("authorString", "Unknown authors"),
            "journal": journal,
            "year": r.get("pubYear", "Unknown year"),
            "citationCount": r.get("citedByCount", 0),
            "isOpenAccess": r.get("isOpenAccess") == "Y",
            "hasTextMinedTerms": r.get("hasTextMinedTerms") == "Y",
            "hasFullText": has_full_text,
            "hasPdfUrl": has_pdf or has_full_text,
            "pdfUrl": None,  # Resolved on demand during paper fetch.
            "abstract": r.get("abstractText", ""),
            "source": "Europe PMC",
        }

    @classmethod
    async def fetch_by_identifier(cls, id_type: str, id_value: str) -> dict[str, Any] | None:
        """Lookup a paper record by exact DOI, PMCID, or PMID.

        Uses fielded identifier queries (DOI:, PMCID:, EXT_ID:) to
        guarantee exact record matching without keyword ambiguity.

        Args:
            id_type: Identifier kind ('doi', 'pmcid', or 'pmid').
            id_value: Normalized identifier value.

        Returns:
            Formatted record dictionary, or None if not found.
        """
        if id_type not in ("doi", "pmcid", "pmid") or not id_value:
            return None

        params = {
            "query": cls._build_search_query(id_type, id_value),
            "format": "json",
            "resultType": "core",
            "pageSize": 1,
        }
        try:
            client = await HttpClientManager.get_client()
            response = await client.get(f"{cls.BASE_URL}/search", params=params, timeout=30.0)
            response.raise_for_status()
            results = response.json().get("resultList", {}).get("result", [])
            if not results:
                return None
            return cls._format_search_result(results[0])
        except Exception as e:
            logger.warning(f"Europe PMC identifier lookup failed for {id_type}:{id_value}: {e}")
            return None

    @staticmethod
    def _normalize_remote_url(url: str) -> str:
        if url.startswith("ftp://"):
            return "https://" + url[len("ftp://") :]
        return url

    @staticmethod
    def _build_pdf_filename(title: str, doi: str, pmcid: str, fallback_id: str) -> str:
        base = title.strip() or doi.strip() or pmcid.strip() or fallback_id.strip() or "paper"
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
        if not safe:
            safe = "paper"
        return f"{safe[:120]}.pdf"

    @staticmethod
    def parse_identifier(raw_input: str) -> tuple[str, str]:
        """Parse bibliographic identifiers into canonical type and value.

        Accepts DOI URLs, resolver prefixes, PMC/PubMed URLs, and bare
        identifiers (e.g., '10.1038/...', 'PMC12345', '12345678').

        Args:
            raw_input: Raw identifier string or URL.

        Returns:
            Tuple of (id_type, normalized_value), or (None, raw_input).
        """
        val = raw_input.strip()

        m = re.match(
            r"https?://(?:www\.)?europepmc\.org/article/PMC/(PMC\d+)",
            val,
            re.IGNORECASE,
        )
        if m:
            return "pmcid", m.group(1)
        m = re.match(r"https?://(?:www\.)?europepmc\.org/article/MED/(\d+)", val, re.IGNORECASE)
        if m:
            return "pmid", m.group(1)

        m = re.match(r"https?://(?:www\.)?pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", val, re.IGNORECASE)
        if m:
            return "pmid", m.group(1)

        # Match NCBI PMC URLs across standard and legacy subdomains.
        m = re.match(
            r"https?://(?:www\.)?ncbi\.nlm\.nih\.gov/pmc/articles/(PMC\d+)",
            val,
            re.IGNORECASE,
        )
        if m:
            return "pmcid", m.group(1).upper()
        m = re.match(
            r"https?://pmc\.ncbi\.nlm\.nih\.gov/articles/(PMC\d+)",
            val,
            re.IGNORECASE,
        )
        if m:
            return "pmcid", m.group(1).upper()

        m = re.match(r"https?://(dx\.)?doi\.org/(.+)", val)
        if m:
            return "doi", m.group(2).strip()

        m = re.match(r"^doi:(.+)", val, re.IGNORECASE)
        if m:
            return "doi", m.group(1).strip()

        # Match standalone PMCID tokens.
        if re.match(r"^PMC\d+$", val, re.IGNORECASE):
            return "pmcid", val.upper()

        if val.startswith("10."):
            return "doi", val

        # Treat standalone numeric strings as PubMed IDs.
        if val.isdigit():
            return "pmid", val

        # Validate standard DOI syntax with registrant and suffix.
        if val.startswith("10.") and "/" in val:
            return "doi", val

        # Return unparsed input when no identifier pattern matches.
        return None, val

    @classmethod
    async def fetch_full_text(cls, pmcid: str) -> str | None:
        """Fetch full text XML from Europe PMC with NCBI fallback.

        Args:
            pmcid: PubMed Central identifier string.

        Returns:
            JATS XML string if available, or None.
        """
        # Attempt retrieval from Europe PMC REST endpoint.
        url = f"{cls.BASE_URL}/{pmcid}/fullTextXML"
        try:
            client = await HttpClientManager.get_client()
            response = await client.get(url, timeout=30.0)
            if response.status_code == 200 and response.text.strip():
                return response.text
        except Exception as e:
            logger.debug(f"Europe PMC full text failed for {pmcid}: {e}")

        # Fall back to NCBI when Europe PMC misses.
        pmc_id = pmcid.replace("PMC", "")
        url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
        params = {"db": "pmc", "id": pmc_id, "rettype": "xml", "retmode": "xml"}
        try:
            client = await HttpClientManager.get_client()
            response = await client.get(url, params=params, timeout=30.0)
            if response.status_code == 200 and response.text.strip():
                # Strip NCBI envelope to extract clean JATS XML.
                if "<pmc-articleset>" in response.text:
                    text = response.text
                    text = text.replace("<pmc-articleset>", "").replace("</pmc-articleset>", "")
                    # Ensure XML declaration and root element exist.
                    if not text.startswith("<?xml"):
                        text = '<?xml version="1.0"?>\n<article>\n' + text + "\n</article>"
                    return text
                return response.text
        except Exception as e:
            logger.debug(f"NCBI PMC full text failed for {pmcid}: {e}")

        return None

    @classmethod
    async def resolve_pdf_url(cls, identifier: str) -> dict[str, str] | None:
        """Resolve a direct downloadable PDF URL for a paper identifier.

        Args:
            identifier: Raw DOI, PMCID, PMID, or paper URL.

        Returns:
            Mapping with url, filename, and source, or None if absent.
        """
        id_type, id_value = cls.parse_identifier(identifier)
        search_url = f"{cls.BASE_URL}/search"
        params = {
            "query": cls._build_search_query(id_type, id_value),
            "format": "json",
            "resultType": "core",
        }

        client = await HttpClientManager.get_client()
        response = await client.get(search_url, params=params, timeout=30.0)
        response.raise_for_status()
        data = response.json()
        results = data.get("resultList", {}).get("result", [])
        if not results:
            return None

        result = results[0]
        pmcid = (result.get("pmcid") or "").strip()
        doi = (result.get("doi") or "").strip()
        title = (result.get("title") or "").strip()
        filename = cls._build_pdf_filename(title, doi, pmcid, id_value)

        full_text_urls = result.get("fullTextUrlList", {}).get("fullTextUrl", [])
        if isinstance(full_text_urls, dict):
            full_text_urls = [full_text_urls]

        for item in full_text_urls:
            document_style = str(item.get("documentStyle", "")).lower()
            url = str(item.get("url", "")).strip()
            if document_style == "pdf" and url:
                return {
                    "url": cls._normalize_remote_url(url),
                    "filename": filename,
                    "source": str(item.get("site", "Europe PMC")),
                }

        if not pmcid:
            return None

        # Query NCBI open access service if Europe PMC lacks direct PDF.
        oa_url = "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi"
        oa_response = await client.get(oa_url, params={"id": pmcid}, timeout=30.0)
        if oa_response.status_code != 200 or not oa_response.text.strip():
            return None

        try:
            root = ElementTree.fromstring(oa_response.text)
        except ElementTree.ParseError:
            return None

        for link in root.findall(".//record/link"):
            if str(link.get("format", "")).lower() != "pdf":
                continue
            href = (link.get("href") or "").strip()
            if href:
                return {
                    "url": cls._normalize_remote_url(href),
                    "filename": filename,
                    "source": "PMC OA",
                }

        return None

    @classmethod
    async def fetch_paper_data(cls, doi: str) -> tuple[str | None, str]:
        """Fetch paper metadata and content by DOI, PMCID, PMID, or URL.

        Args:
            doi: Raw identifier string or URL.

        Returns:
            Tuple of (content_text, mode) where mode is 'full_text',
            'abstract', 'empty', or 'error'.
        """
        id_type, id_value = cls.parse_identifier(doi)

        # Return cached content and mode when available.
        cached = pmc_cache.get(id_value)
        if cached:
            return cached["text"], cached["mode"]

        try:
            client = await HttpClientManager.get_client()

            # Format query for exact identifier lookup.
            if id_type == "pmcid":
                query = f"PMCID:{id_value}"
            elif id_type == "pmid":
                query = f"EXT_ID:{id_value}"
            else:
                query = f"DOI:{id_value}"

            # Retrieve bibliographic metadata for record details.
            search_url = f"{cls.BASE_URL}/search"
            params = {"query": query, "format": "json", "resultType": "core"}

            response = await client.get(search_url, params=params, timeout=30.0)
            response.raise_for_status()
            data = response.json()

            results = data.get("resultList", {}).get("result", [])
            if not results:
                # Missing records fall through to alternate providers.
                return None, "error"

            result = results[0]
            pmcid = result.get("pmcid")
            is_oa = result.get("isOpenAccess") == "Y"
            abstract = result.get("abstractText", "")
            title = result.get("title", "")
            doi = result.get("doi", "")
            # Extract journal title from primary or nested metadata.
            journal = result.get("journalTitle", "")
            if not journal:
                journal_info = result.get("journalInfo", {})
                if journal_info:
                    journal = journal_info.get("journal", {}).get("title", "")
            # Parse author list from comma-delimited string.
            authors_str = result.get("authorString", "")
            authors = (
                [a.strip() for a in authors_str.split(",") if a.strip()] if authors_str else []
            )
            # Prefer first publication date over issue release date.
            raw_date = result.get("firstPublicationDate") or result.get("publicationDate", "")
            if raw_date:
                try:
                    # Europe PMC may return YYYY-MM-DD, YYYY-MM, or YYYY
                    parts = raw_date.split("-")
                    if len(parts) == 3:
                        dt = datetime.strptime(raw_date, "%Y-%m-%d")
                        pub_date = dt.strftime("%d %B %Y")
                    elif len(parts) == 2:
                        dt = datetime.strptime(raw_date, "%Y-%m")
                        pub_date = dt.strftime("%d %B %Y")
                    else:
                        pub_date = raw_date
                except Exception:
                    pub_date = raw_date
            if not pub_date:
                # Fallback to journal issue year and month.
                journal_info = result.get("journalInfo", {})
                month = journal_info.get("monthOfPublication")
                year = journal_info.get("yearOfPublication")
                if month and year:
                    try:
                        if isinstance(month, int):
                            month_num = month
                        else:
                            month_num = datetime.strptime(month[:3], "%b").month
                        pub_date = datetime.strptime(f"{year}-{month_num:02d}", "%Y-%m").strftime(
                            "%d %B %Y"
                        )
                    except Exception:
                        pub_date = str(year)
                elif year:
                    pub_date = str(year)
                else:
                    pub_year = result.get("pubYear")
                    if pub_year:
                        pub_date = str(pub_year)

            # Extract full-text XML when PMCID is available.
            if pmcid:
                full_text = await cls.fetch_full_text(pmcid)
                if full_text:
                    pmc_cache.set(
                        id_value,
                        {
                            "text": full_text,
                            "mode": "full_text",
                            "title": title,
                            "journal": journal,
                            "doi": doi,
                            "authors": authors,
                            "date": pub_date,
                            "is_oa": is_oa,
                        },
                    )
                    return full_text, "full_text"

            # Fall back to abstract when full text is missing.
            if abstract:
                logger.info(f"Falling back to abstract for {id_type.upper()}: {id_value}")
                pmc_cache.set(
                    id_value,
                    {
                        "text": abstract,
                        "mode": "abstract",
                        "title": title,
                        "journal": journal,
                        "doi": doi,
                        "authors": authors,
                        "date": pub_date,
                        "is_oa": is_oa,
                    },
                )
                return abstract, "abstract"

            logger.debug(f"Empty content for {id_type.upper()}: {id_value}")
            # Cache empty state to prevent redundant downstream queries.
            try:
                pmc_cache.set(
                    id_value,
                    {
                        "text": None,
                        "mode": "empty",
                        "title": title,
                        "journal": journal,
                        "authors": authors,
                        "doi": doi,
                        "date": pub_date,
                        "is_oa": is_oa,
                    },
                )
            except Exception:
                pass
            return None, "empty"

        except Exception as e:
            logger.error(f"Error fetching from Europe PMC: {e}")
            return None, "error"

    @classmethod
    async def search_literature(
        cls,
        query: str,
        filters: dict,
        max_results: int = 25,
        sort: str = "",
        cursor_mark: str = "*",
    ) -> dict[str, Any]:
        """Search literature using fielded filters and cursor pagination.

        Args:
            query: Free-text search terms.
            filters: Filter dictionary for open access, text, and type.
            max_results: Page size for retrieved records.
            sort: Sort criteria ('cited', 'date', or 'date_asc').
            cursor_mark: Cursor token for Europe PMC pagination.

        Returns:
            Mapping of formatted results and pagination metadata.
        """
        search_parts = []
        if query and query.strip():
            search_parts.append(f"({query.strip()})")

        if filters.get("open_access"):
            search_parts.append("OPEN_ACCESS:y")
        if filters.get("has_full_text"):
            search_parts.append("HAS_FT:y")
        if filters.get("article_type"):
            search_parts.append(f'PUB_TYPE:"{filters["article_type"]}"')

        # Descending sorts use query terms; ascending uses sort param.
        sort_param = ""
        if sort == "cited":
            search_parts.append("sort_cited:y")
        elif sort == "date":
            search_parts.append("sort_date:y")
        elif sort == "date_asc":
            sort_param = "P_PDATE_D asc"

        final_query = " AND ".join(search_parts)
        if not final_query:
            return {
                "results": [],
                "pagination": {
                    "total": 0,
                    "cursorMark": "*",
                    "nextCursorMark": "",
                    "hasMore": False,
                    "pageSize": max_results,
                },
            }
        search_url = f"{cls.BASE_URL}/search"
        params = {
            "query": final_query,
            "format": "json",
            "resultType": "core",
            "pageSize": max_results,
            "cursorMark": cursor_mark,
        }
        if sort_param:
            params["sort"] = sort_param

        max_retries = 3
        base_delay = 1.5
        max_retry_delay = 10.0

        try:
            client = await HttpClientManager.get_client()

            response = None
            for attempt in range(max_retries):
                try:
                    response = await client.get(search_url, params=params, timeout=30.0)
                    response.raise_for_status()
                    break
                except httpx.HTTPStatusError as e:
                    status_code = e.response.status_code if e.response is not None else None
                    is_transient = status_code is not None and 500 <= status_code < 600
                    if not is_transient or attempt == max_retries - 1:
                        raise

                    retry_after_header = e.response.headers.get("retry-after")
                    if retry_after_header:
                        try:
                            delay = min(float(retry_after_header), max_retry_delay)
                        except ValueError:
                            delay = base_delay * (2**attempt)
                    else:
                        delay = base_delay * (2**attempt)

                    logger.warning(
                        "Europe PMC search failed with %s. Retrying in %.1fs (attempt %s/%s)",
                        status_code,
                        delay,
                        attempt + 1,
                        max_retries,
                    )
                    await asyncio.sleep(delay)
                except (httpx.TimeoutException, httpx.RequestError) as e:
                    if attempt == max_retries - 1:
                        raise

                    delay = base_delay * (2**attempt)
                    logger.warning(
                        "Europe PMC search transient error (%s). Retrying in %.1fs (attempt %s/%s)",
                        type(e).__name__,
                        delay,
                        attempt + 1,
                        max_retries,
                    )
                    await asyncio.sleep(delay)

            if response is None:
                raise RuntimeError("Europe PMC search returned no response after retries")

            data = response.json()

            # Extract pagination cursors for subsequent batch requests.
            hit_count = data.get("hitCount", 0)
            next_cursor = data.get("nextCursorMark", "")
            has_more = next_cursor != cursor_mark and next_cursor != ""

            results = data.get("resultList", {}).get("result", [])
            formatted_results = [cls._format_search_result(r) for r in results]
            return {
                "results": formatted_results,
                "pagination": {
                    "total": hit_count,
                    "cursorMark": cursor_mark,
                    "nextCursorMark": next_cursor,
                    "hasMore": has_more,
                    "pageSize": max_results,
                },
            }
        except Exception as e:
            logger.error(f"Error in search_literature: {e}")
            raise
