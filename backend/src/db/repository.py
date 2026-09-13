"""Database repository providing access queries for papers and entities.

Centralizes database queries for papers, entity facts, metrics, and
aggregation logic away from HTTP endpoint routing layers.
"""

import json
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.db.models import Paper, PaperEntity


def _maybe_json_load(value: Any) -> Any:
    """Deserialize a JSON-encoded string if needed, returning None on error.

    Handles payloads stored as text or already deserialized objects.
    """
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None
    return value


def entity_row_to_dict(pe: PaperEntity) -> dict:
    """Transform a PaperEntity ORM record into a flattened dictionary.

    Unpacks ingestion metadata keys and normalizes aliases into a list
    suitable for API responses.
    """
    meta_raw = _maybe_json_load(pe.meta)
    metadata: dict = meta_raw if isinstance(meta_raw, dict) else {}

    aliases = metadata.pop("aliases", None) if isinstance(metadata, dict) else None
    if not isinstance(aliases, list):
        aliases = []

    return {
        "label": pe.label,
        "text": pe.canonical_text,
        "canonical": pe.canonical_text,
        "count": pe.frequency,
        "aliases": aliases,
        **metadata,
    }


async def dashboard_metrics(db: AsyncSession) -> dict:
    """Compute aggregate dashboard metrics, distributions, and KPIs.

    Calculates distinct paper counts, unique entity frequencies,
    journal rankings, yearly volume, and geographic distribution across
    locations.
    """

    total_papers = (await db.execute(select(func.count(Paper.id)))).scalar() or 0

    total_entities = (
        await db.execute(
            select(func.count()).select_from(
                select(PaperEntity.label, PaperEntity.canonical_text).distinct().subquery()
            )
        )
    ).scalar() or 0

    total_journals = (
        await db.execute(
            select(func.count(func.distinct(Paper.journal))).where(Paper.journal.is_not(None))
        )
    ).scalar() or 0

    top_3_journals_rows = (
        await db.execute(
            select(Paper.journal)
            .where(Paper.journal.is_not(None))
            .group_by(Paper.journal)
            .order_by(desc(func.count(Paper.id)))
            .limit(3)
        )
    ).all()
    top_3_journals = [row[0] for row in top_3_journals_rows]

    papers_by_journal_rows = (
        await db.execute(
            select(Paper.journal, func.count(Paper.id).label("count"))
            .where(Paper.journal.is_not(None))
            .group_by(Paper.journal)
            .order_by(desc("count"))
        )
    ).all()
    papers_by_journal = [{"name": row[0], "value": row[1]} for row in papers_by_journal_rows]

    entity_distribution_rows = (
        await db.execute(
            select(
                PaperEntity.label,
                func.count(func.distinct(PaperEntity.canonical_text)).label("count"),
            )
            .group_by(PaperEntity.label)
            .order_by(desc("count"))
        )
    ).all()
    entity_distribution = [
        {"name": (row[0] or "").title(), "value": row[1]} for row in entity_distribution_rows
    ]

    papers_by_year_rows = (
        await db.execute(
            select(Paper.year, func.count(Paper.id).label("count"))
            .where(Paper.year.is_not(None))
            .group_by(Paper.year)
            .order_by(Paper.year)
        )
    ).all()
    papers_by_year = [{"name": str(row[0]), "value": row[1]} for row in papers_by_year_rows]

    country_expr = func.json_extract(PaperEntity.meta, "$.country").label("country")
    geo_rows = (
        await db.execute(
            select(
                country_expr,
                func.count(func.distinct(PaperEntity.paper_id)).label("count"),
            )
            .where(PaperEntity.label == "LOCATION")
            .where(country_expr.is_not(None))
            .group_by("country")
            .order_by(desc("count"))
        )
    ).all()
    geo_distribution = [{"name": row[0], "value": row[1]} for row in geo_rows if row[0]]

    return {
        "kpis": {
            "total_papers": total_papers,
            "total_entities": total_entities,
            "total_journals": total_journals,
            "top_journals": ", ".join(top_3_journals),
        },
        "charts": {
            "papers_by_journal": papers_by_journal,
            "entity_distribution": entity_distribution,
            "papers_by_year": papers_by_year,
            "geo_distribution": geo_distribution,
        },
    }


async def list_papers(
    db: AsyncSession,
    *,
    limit: int,
    offset: int,
    country: str | None,
    query: str | None,
    year: int | None,
) -> tuple[int, list[dict[str, Any]]]:
    """Retrieve a paginated list of papers matching optional filters.

    Supports filtering by geographical location country, search terms
    matching title or journal, and publication year. Returns total match
    count alongside the paginated records.
    """
    select_stmt = select(Paper)

    if country:
        country_expr = func.json_extract(PaperEntity.meta, "$.country")
        select_stmt = (
            select_stmt.join(PaperEntity)
            .where(PaperEntity.label == "LOCATION")
            .where(country_expr == country)
        )

    if query:
        select_stmt = select_stmt.where(
            Paper.title.ilike(f"%{query}%") | Paper.journal.ilike(f"%{query}%")
        )

    if year is not None:
        select_stmt = select_stmt.where(Paper.year == year)

    select_stmt = select_stmt.group_by(Paper.id).order_by(desc(Paper.id))

    result = await db.execute(select_stmt.limit(limit).offset(offset))
    papers = result.scalars().all()

    if country or query or year is not None:
        subq = select_stmt.limit(None).offset(None).subquery()
        count_stmt = select(func.count()).select_from(subq)
    else:
        count_stmt = select(func.count(Paper.id))

    count_result = await db.execute(count_stmt)
    total_count = count_result.scalar() or 0

    paper_list = [
        {
            "id": p.id,
            "doi": p.doi,
            "title": p.title,
            "journal": p.journal,
            "year": p.year,
            "is_open_access": p.is_open_access,
            "entity_count": p.entity_count,
        }
        for p in papers
    ]
    return total_count, paper_list


async def paper_id_by_doi(
    db: AsyncSession,
    clean_id: str,
    raw_doi: str,
) -> int | None:
    """Look up paper primary key by normalized or raw DOI string.

    Tries the normalized DOI first, falling back to the raw DOI to
    handle legacy unnormalized DOI entries.
    """
    result = await db.execute(select(Paper.id).where(Paper.doi == clean_id))
    paper_id = result.scalar_one_or_none()
    if not paper_id and clean_id != raw_doi:
        result = await db.execute(select(Paper.id).where(Paper.doi == raw_doi))
        paper_id = result.scalar_one_or_none()
    return paper_id


async def entities_for_paper(db: AsyncSession, paper_id: int) -> list[dict]:
    """Retrieve all entity facts associated with a paper as dictionaries.

    Returns the paper's annotated entities formatted for API output.
    """
    result = await db.execute(select(PaperEntity).where(PaperEntity.paper_id == paper_id))
    return [entity_row_to_dict(pe) for pe in result.scalars().all()]
