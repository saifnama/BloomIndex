"""OpenAlex HTTP API client.

Fetches scientific paper metadata by DOI from the OpenAlex Works API,
reconstructing inverted abstract indexes and resolving open access URLs.
"""

import logging
from typing import Any

from backend.src.common.http_client import HttpClientManager

logger = logging.getLogger(__name__)

BASE_URL = "https://api.openalex.org/works"


def _reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str:
    """Reconstruct contiguous abstract text from an inverted word index.

    Args:
        inverted_index: Mapping of words to token position lists.

    Returns:
        Reassembled abstract prose string.
    """
    if not inverted_index:
        return ""
    # Map token positions to words, keeping earlier duplicate entries.
    position_to_word: dict[int, str] = {}
    for word, positions in inverted_index.items():
        for pos in positions:
            if pos not in position_to_word:
                position_to_word[pos] = word
    if not position_to_word:
        return ""
    # Reassemble ordered word sequence across index positions.
    max_pos = max(position_to_word.keys())
    words = [position_to_word.get(i, "") for i in range(max_pos + 1)]
    return " ".join(words).strip()


class OpenAlexClient:
    """HTTP client for querying work records in the OpenAlex API."""

    BASE_URL = BASE_URL

    @classmethod
    async def fetch_paper(cls, doi: str) -> dict[str, Any]:
        """Fetch normalized paper metadata by DOI from OpenAlex.

        Args:
            doi: Raw DOI string or resolver URL.

        Returns:
            Dictionary of paper metadata, or empty dict on failure.
        """
        doi = doi.strip().lower()
        if doi.startswith("https://doi.org/"):
            doi = doi.replace("https://doi.org/", "")
        elif doi.startswith("http://doi.org/"):
            doi = doi.replace("http://doi.org/", "")
        elif doi.startswith("doi:"):
            doi = doi[4:].strip()

        if not doi:
            return {}

        url = f"{cls.BASE_URL}/doi:{doi}"
        try:
            client = await HttpClientManager.get_client()
            response = await client.get(url, timeout=15.0)
            if response.status_code != 200:
                logger.warning(f"OpenAlex returned {response.status_code} for DOI: {doi}")
                return {}
            data = response.json()

            title = data.get("title", "")
            if not title:
                return {}

            # Reconstruct abstract text from inverted index mapping.
            abstract_index = data.get("abstract_inverted_index")
            abstract = _reconstruct_abstract(abstract_index)

            # Prefer primary open access location for PDF URL.
            best_oa = data.get("best_oa_location") or {}
            pdf_url = best_oa.get("pdf_url") or next(
                (
                    loc.get("pdf_url")
                    for loc in data.get("locations", []) or []
                    if loc.get("pdf_url")
                ),
                "",
            )

            return {
                "doi": doi,
                "title": title,
                "abstract": abstract,
                "authors": [
                    a.get("author", {}).get("display_name", "")
                    for a in data.get("authorships", [])
                    if a.get("author", {}).get("display_name")
                ][:10],
                "year": data.get("publication_year"),
                "journal": data.get("primary_location", {})
                .get("source", {})
                .get("display_name", ""),
                "pmcid": data.get("ids", {}).get("pmcid", ""),
                "pmid": "",
                "source": "OpenAlex",
                "url": data.get("doi"),
                "pdfUrl": pdf_url,
                "isOpenAccess": bool((data.get("open_access") or {}).get("is_oa")),
            }
        except Exception as e:
            logger.error(f"OpenAlex fetch failed for {doi}: {e}")
            return {}
