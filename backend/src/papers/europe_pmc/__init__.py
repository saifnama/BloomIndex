"""Europe PMC literature retrieval package.

Exposes the primary Europe PMC service interface and text sanitization
utilities for article searching and XML content ingestion.
"""

from backend.src.common.sanitizer import sanitize
from backend.src.papers.europe_pmc.service import EuropePMCService

__all__ = ["EuropePMCService", "sanitize"]
