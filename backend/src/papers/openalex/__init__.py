"""OpenAlex literature retrieval package.

Exposes the primary OpenAlex service interface for bibliographic
search and work metadata retrieval.
"""

from backend.src.papers.openalex.service import OpenAlexService

__all__ = ["OpenAlexService"]
