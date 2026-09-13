"""Importer for the two-table schema (papers + paper_entities).

Reads the curated Excel sheet from a CLI path argument or the
EXCEL_PATH environment variable. Groups rows by DOI, upserts a
Paper row per DOI (falling back to OpenAlex for missing metadata),
then upserts a paper_entities row per (label, canonical_text):

- On first insert: frequency = 1 and metadata is the merged
  dictionary and Excel metadata dict, or NULL if nothing useful.
- On conflict (re-import of an already-seen tuple):
  frequency += 1 and metadata is kept as-is, with the existing
  non-NULL value winning via COALESCE.

Labels imported: CHEMICAL, SPECIES, PLANT PART, ANALYTICAL TECHNIQUE,
EXTRACTION METHOD, DEVELOPMENT STAGE, LOCATION.
BIOACTIVITY, DISEASE, and SEASON are intentionally excluded.

Run via:
    python -m backend.src.db.importer path/to/data.xlsx
"""

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.src.db.models import Paper, PaperEntity
from backend.src.db.session import AsyncSessionLocal, Base, engine
from backend.src.ner.dictionaries.chemical import get_matcher as get_chemical_matcher
from backend.src.ner.dictionaries.species import get_matcher as get_species_matcher

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Environment variable override for the curated Excel dataset path.
# Can also be provided directly via CLI argument or function parameter.
EXCEL_PATH = os.environ.get("EXCEL_PATH")


# Keys excluded from the metadata JSON column: internal dictionary
# fields and Excel scratch columns that carry no query value.
DROPPED_FIELDS = {
    "text",
    "span",
    "type",
    "label",
    "score",
    "linked_to",
    "canonical",
    "start",
    "end",
    "aliases",
}


# --- OpenAlex fallback ---


async def fetch_paper_metadata(doi: str):
    """Fetch title, journal, year, and OA flag from OpenAlex.

    Used only when the Excel sheet does not provide those fields.
    Returns None on HTTP failure or network error.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            url = f"https://api.openalex.org/works/https://doi.org/{doi}"
            response = await client.get(url)
            if response.status_code == 200:
                data = response.json()
                return {
                    "title": data.get("title"),
                    "journal": data.get("host_venue", {}).get("display_name")
                    or data.get("primary_location", {}).get("source", {}).get("display_name"),
                    "year": data.get("publication_year"),
                    "is_oa": data.get("open_access", {}).get("is_oa", False),
                }
    except Exception as e:
        logger.warning(f"Metadata fetch failed for {doi}: {e}")
    return None


# --- DB lifecycle ---


async def init_db():
    """Create tables if missing.

    Uses CREATE TABLE IF NOT EXISTS, so it is safe to call against
    an already-populated database.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database initialized.")


# --- Excel row to entity descriptors ---


def _cell(row, col) -> str | None:
    """Return a stripped string for row[col], or None for blanks/NaN."""
    if col not in row.index:
        return None
    val = row[col]
    if pd.isna(val):
        return None
    s = str(val).strip()
    if not s or s.lower() == "nan":
        return None
    return s


def get_entity_mappings(row):
    """Map a single Excel row to a list of entity descriptors.

    Each descriptor is a dict with keys: label, canonical_text,
    and excel_meta (additional columns from the sheet).

    SPECIES text is preserved as-written to match existing DB rows
    (e.g. "Lantana camara"). All other labels are lowercased to
    match the dictionary convention (e.g. "alpha-pinene", "gc-ms").
    """
    entities = []

    def add_entity(label: str, text_col: str, meta_cols: dict[str, str] | None = None):
        text = _cell(row, text_col)
        if not text:
            return
        excel_meta: dict[str, Any] = {}
        if meta_cols:
            for meta_key, meta_col in meta_cols.items():
                val = _cell(row, meta_col)
                if val:
                    excel_meta[meta_key] = val
        if label == "SPECIES":
            canonical = text  # preserve as-written to match existing DB rows
        else:
            canonical = text.lower()
        entities.append(
            {
                "label": label,
                "canonical_text": canonical,
                "excel_meta": excel_meta,
            }
        )

    # BIOACTIVITY, DISEASE, and SEASON are excluded by scope decision.
    # The Excel sheet may have those columns; they are ignored here.
    add_entity("SPECIES", "Scientific_Name", {"family": "Family", "common_name": "Common name"})
    add_entity("CHEMICAL", "Chemical")
    add_entity("PLANT PART", "Plant Part")
    add_entity("DEVELOPMENT STAGE", "Development stage")
    add_entity("EXTRACTION METHOD", "Extraction Method")
    add_entity("ANALYTICAL TECHNIQUE", "Analytical Technique")

    # Fall back to Country when the Location column is blank.
    loc_text = _cell(row, "Location")
    country = _cell(row, "Country")
    if not loc_text and country:
        loc_text = country
    if loc_text:
        loc_meta: dict[str, Any] = {}
        if country:
            loc_meta["country"] = country
        entities.append(
            {
                "label": "LOCATION",
                "canonical_text": loc_text.title(),
                "excel_meta": loc_meta,
            }
        )

    return entities


# --- Dictionary enrichment and metadata merge ---


def _enrich_from_dictionary(
    label: str, canonical_text: str, chem_matcher, species_matcher
) -> dict[str, Any]:
    """Look the entity up in the appropriate dictionary (CHEMICAL or SPECIES).

    Returns the dictionary's metadata dict, or {} for unknown labels
    or unknown terms. Tries canonical_text first, then a lowercased
    variant to handle case mismatches with the dictionary.
    """
    if label not in ("CHEMICAL", "SPECIES"):
        return {}

    matcher = chem_matcher if label == "CHEMICAL" else species_matcher
    canonical_lower = canonical_text.lower().strip()

    try:
        result = matcher.lookup(canonical_text)
        if result is None and canonical_lower != canonical_text:
            result = matcher.lookup(canonical_lower)
        return result or {}
    except Exception as e:
        logger.warning(f"Dictionary lookup failed for {label} '{canonical_text}': {e}")
        return {}


def _build_metadata(
    dictionary_meta: dict[str, Any], excel_meta: dict[str, Any]
) -> dict[str, Any] | None:
    """Merge dictionary and Excel metadata for the paper_entities JSON column.

    Excel values win on key conflicts because they are manually curated.
    Returns None when the merged result contains nothing useful, so the
    column stays SQL NULL rather than storing an empty object.
    """
    merged: dict[str, Any] = {}
    if dictionary_meta:
        merged.update(dictionary_meta)
    if excel_meta:
        for k, v in excel_meta.items():
            if v is not None and v != "":
                merged[k] = v

    cleaned = {
        k: v for k, v in merged.items() if k not in DROPPED_FIELDS and v is not None and v != ""
    }
    return cleaned if cleaned else None


# --- paper_entities UPSERT ---


async def upsert_paper_entity(
    session,
    paper_id: int,
    label: str,
    canonical_text: str,
    metadata: dict[str, Any] | None,
) -> None:
    """Insert one paper_entities row, or on conflict bump frequency.

    The UNIQUE INDEX on (paper_id, label, canonical_text) triggers the
    ON CONFLICT clause. Existing metadata is preserved via COALESCE:
    a non-NULL value already in the row is never overwritten.
    """
    # Pass the dict, not a pre-serialized JSON string: the column type
    # serializes on bind. A pre-dumped string double-encodes the value,
    # which makes SQLite's json_extract() return NULL, so those rows
    # silently disappear from geo charts and country filters.
    # set_ keys and excluded attrs must use the SQL column name
    # ("metadata"), not the Python attribute name ("meta").
    stmt = sqlite_insert(PaperEntity).values(
        paper_id=paper_id,
        label=label,
        canonical_text=canonical_text,
        frequency=1,
        meta=metadata,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[
            PaperEntity.paper_id,
            PaperEntity.label,
            PaperEntity.canonical_text,
        ],
        set_={
            "frequency": PaperEntity.frequency + 1,
            "metadata": func.coalesce(PaperEntity.meta, stmt.excluded.metadata),
        },
    )
    await session.execute(stmt)


# --- Main flow ---


async def import_data(excel_path: str | Path | None = None):
    """Run the full import pipeline into the database.

    Initializes tables, loads dictionary matchers, reads the Excel file,
    and upserts Paper and PaperEntity rows grouped by DOI.

    Parameters:
        excel_path: Path to the curated Excel file. Defaults to the
            EXCEL_PATH environment variable if not passed.
    """
    raw_path = excel_path or EXCEL_PATH
    if not raw_path:
        raise ValueError(
            "No Excel path provided. Pass path as an argument, "
            "e.g. `import_data('path/to/data.xlsx')`, or set the "
            "EXCEL_PATH environment variable."
        )

    target_path = Path(raw_path).expanduser()
    if not target_path.exists():
        raise FileNotFoundError(
            f"Excel file not found at: {target_path}. "
            "Pass a valid path as a CLI argument or set EXCEL_PATH."
        )

    await init_db()

    # Each matcher loads a .pkl cache on first call. One shared instance
    # per run avoids redundant disk I/O on subsequent lookups.
    logger.info("Loading dictionary matchers (chemical + species)...")
    chem_matcher = get_chemical_matcher()
    species_matcher = get_species_matcher()
    logger.info("Dictionary matchers ready.")

    logger.info(f"Reading Excel file: {target_path}")
    df = pd.read_excel(target_path)
    df = df.dropna(subset=["DOI number"])

    async with AsyncSessionLocal() as session:
        grouped = df.groupby("DOI number")

        for doi, group in grouped:
            doi_str = str(doi).strip()

            # 1. Get or create Paper
            result = await session.execute(select(Paper).where(Paper.doi == doi_str))
            paper = result.scalars().first()

            if not paper:
                first_row = group.iloc[0]

                excel_title = (
                    str(first_row.get("Title")) if pd.notna(first_row.get("Title")) else None
                )
                excel_journal = (
                    str(first_row.get("Journal")) if pd.notna(first_row.get("Journal")) else None
                )
                excel_year = (
                    int(first_row.get("Year of data collection"))
                    if pd.notna(first_row.get("Year of data collection"))
                    else None
                )
                excel_oa = False
                if "Open Access" in first_row.index and pd.notna(first_row.get("Open Access")):
                    val = str(first_row.get("Open Access")).lower()
                    excel_oa = val in ("yes", "true", "1", "open")

                api_meta = await fetch_paper_metadata(doi_str)

                # Manual metadata override for a paper whose
                # OpenAlex record is incomplete or unavailable.
                if doi_str == "10.1080/0972060X.2011.10643597":
                    title = (
                        "Chemical Composition and Cytotoxic Activity of "
                        "Essential Oil of Chromolaena odorata L. Growing in Togo"
                    )
                    journal = "Journal of Essential Oil Bearing Plants"
                    year = 2011
                    is_oa = True
                else:
                    title = excel_title or (api_meta.get("title") if api_meta else None)
                    journal = excel_journal or (api_meta.get("journal") if api_meta else None)
                    year = excel_year or (api_meta.get("year") if api_meta else None)
                    is_oa = excel_oa or (api_meta.get("is_oa") if api_meta else False)

                paper = Paper(
                    doi=doi_str,
                    title=title,
                    journal=journal,
                    year=year,
                    is_open_access=is_oa,
                    entity_count=0,
                )
                session.add(paper)
                await session.flush()  # populate paper.id

            # 2. UPSERT each entity for every row of this DOI.
            row_mentions = 0
            for _, row in group.iterrows():
                mappings = get_entity_mappings(row)
                for em in mappings:
                    label = em["label"]
                    canonical = em["canonical_text"]
                    dictionary = _enrich_from_dictionary(
                        label, canonical, chem_matcher, species_matcher
                    )
                    metadata = _build_metadata(dictionary, em["excel_meta"])
                    await upsert_paper_entity(session, paper.id, label, canonical, metadata)
                    row_mentions += 1

            # 3. Refresh entity_count from the live table.
            paper.entity_count = await session.scalar(
                select(func.count())
                .select_from(PaperEntity)
                .where(PaperEntity.paper_id == paper.id)
            )

            logger.info(
                f"Imported DOI: {doi_str} - processed {row_mentions}"
                f" row-mentions, paper now linked to"
                f" {paper.entity_count} distinct entities."
            )

        await session.commit()
        logger.info("Import complete.")


if __name__ == "__main__":
    cli_path = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(import_data(cli_path))
