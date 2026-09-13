"""DOI abstract resolution router.

Fallback endpoint to fetch abstracts for DOIs not indexed or missing
abstract content in Europe PMC.
"""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from backend.src.common.caching import doi_cache
from backend.src.papers.europe_pmc import EuropePMCService
from backend.src.papers.resolver import fetch_doi_abstract

router = APIRouter(prefix="/doi", tags=["doi"])


def _normalize_doi(doi: str) -> str:
    """Strip protocol and resolver prefixes from DOI string."""
    normalized = doi.strip().lower()
    if normalized.startswith("https://doi.org/"):
        normalized = normalized.replace("https://doi.org/", "")
    elif normalized.startswith("http://doi.org/"):
        normalized = normalized.replace("http://doi.org/", "")
    elif normalized.startswith("doi:"):
        normalized = normalized[4:].strip()
    return normalized


def _strip_cache_metadata(payload: dict) -> dict:
    """Filter internal metadata keys prefixed with underscore."""
    return {key: value for key, value in payload.items() if not key.startswith("_")}


@router.get("/abstract")
async def get_doi_abstract(
    doi: str = Query(..., description="DOI to fetch abstract for"),
):
    """Fetch abstract for a DOI when missing from Europe PMC.

    Queries OpenAlex with fallback to Semantic Scholar and caches
    results.
    """
    id_type, clean_id = EuropePMCService.parse_identifier(doi)
    if id_type != "doi":
        raise HTTPException(status_code=400, detail="This endpoint only accepts DOI input")

    normalized_doi = _normalize_doi(clean_id)
    if not normalized_doi:
        raise HTTPException(status_code=400, detail="Invalid DOI")

    cached = doi_cache.get(normalized_doi)
    if cached:
        return JSONResponse(content=_strip_cache_metadata(cached))

    result = await fetch_doi_abstract(normalized_doi)
    if not result:
        raise HTTPException(status_code=404, detail=f"No abstract found for DOI: {normalized_doi}")

    doi_cache.set(normalized_doi, result)

    return JSONResponse(content=result)
