"""OpenAlex service facade.

Provides unified access to OpenAlex literature metadata retrieval and
abstract reconstruction.
"""

from typing import Any

from backend.src.papers.openalex.client import OpenAlexClient


class OpenAlexService:
    """Service facade delegating work queries to the OpenAlex client."""

    @classmethod
    async def fetch_paper(cls, doi: str) -> dict[str, Any]:
        """Fetch normalized paper metadata for a given DOI.

        Args:
            doi: Raw DOI string or resolver URL.

        Returns:
            Dictionary of paper metadata, or empty dict on error.
        """
        return await OpenAlexClient.fetch_paper(doi)
