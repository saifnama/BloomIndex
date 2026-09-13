"""Scholarly literature search router."""

import logging

from fastapi import APIRouter, Form, HTTPException

from backend.src.search.service import SearchService

router = APIRouter(prefix="/search", tags=["search"])
logger = logging.getLogger(__name__)


@router.post("/json")
async def search_papers_json(
    query: str = Form(""),
    open_access: bool = Form(False),
    has_full_text: bool = Form(False),
    article_type: str = Form(""),
    sort: str = Form(""),
    page: int = Form(1),
    cursor_mark: str = Form("*"),
    source: str = Form("europepmc"),  # europepmc or openalex.
):
    """Search literature using Europe PMC or OpenAlex providers."""
    source = source.lower() if source else "europepmc"
    if source not in ("europepmc", "openalex"):
        source = "europepmc"

    filters = {
        "open_access": open_access,
        "has_full_text": has_full_text,
        "article_type": article_type,
    }
    try:
        return await SearchService.search_literature(
            query=query,
            filters=filters,
            page_size=25,
            page=max(1, page),
            sort=sort,
            source=source,
        )
    except Exception as e:
        logger.error(f"Search failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/types")
async def get_article_types(source: str = "openalex"):
    """Fetch available article types for the requested search source.

    Returns live counts for OpenAlex or static categories for
    Europe PMC.
    """
    source = source.lower() if source else "openalex"

    if source == "openalex":
        try:
            types = await SearchService.get_openalex_type_counts()
            return {"types": types}
        except Exception as e:
            logger.error(f"Failed to fetch OpenAlex types: {e}")
            raise HTTPException(status_code=502, detail=str(e))

    # Europe PMC publication types.
    return {
        "types": [
            {"key": "", "display_name": "Any Type", "count": None},
            {"key": "Research-article", "display_name": "Research Article", "count": None},
            {"key": "Review", "display_name": "Review", "count": None},
        ]
    }
