"""Sync NER CSV data into the SQLite database.

Normalizes denormalized entity rows into papers and paper_entities
tables. Execution is idempotent via INSERT OR IGNORE.
"""

import argparse
import csv
import json
import os
import sqlite3
import sys


def cell(row, *keys):
    """Return the first non-empty stripped value from the given keys."""
    for k in keys:
        v = row.get(k, "")
        if v and v.strip():
            return v.strip()
    return ""


def to_int(v):
    """Parse string to integer, returning None on conversion failure."""
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def build_meta(family, common):
    """Serialize optional taxonomic metadata into a JSON string."""
    obj = {}
    if family:
        obj["family"] = family
    if common:
        obj["common_name"] = common
    return json.dumps(obj) if obj else None


def main():
    parser = argparse.ArgumentParser(description="Sync NER CSV data into SQLite database.")
    parser.add_argument("-i", "--input", required=True, help="Path to CSV file")
    parser.add_argument("-d", "--database", required=True, help="Path to SQLite database")
    args = parser.parse_args()
    csv_path = args.input
    db_path = args.database

    if not os.path.exists(csv_path):
        print(f"Error: CSV file not found: {csv_path}")
        sys.exit(1)
    if not os.path.exists(db_path):
        print(f"Error: Database file not found: {db_path}")
        sys.exit(1)

    # Phase 1: Parse CSV.
    # Map entities by composite key for constant-time deduplication.
    papers = {}
    entities = {}
    paper_dedup = {}

    print("Reading CSV...")
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            doi = cell(row, "DOI")
            if not doi:
                continue

            # Paper metadata: first row in CSV defines paper attributes.
            if doi not in papers:
                papers[doi] = {
                    "doi": doi,
                    "title": cell(row, "fetched_title"),
                    "journal": cell(row, "journal", "Journal"),
                    "year": to_int(row.get("publication_year")),
                    "is_open_access": 1
                    if str(row.get("is_open_access", "")).upper() in ("TRUE", "1", "YES")
                    else 0,
                }
                paper_dedup[doi] = set()

            # CHEMICAL — one per row, frequency counted across rows
            compound = cell(row, "Compound_name")
            if compound:
                key = (doi, "CHEMICAL", compound)
                if key not in entities:
                    entities[key] = {"freq": 0, "meta": None}
                entities[key]["freq"] += 1

            # Deduplicate paper attributes repeated across entity rows.
            species = cell(row, "Scientific_name")
            if species and ("SPECIES", species) not in paper_dedup[doi]:
                meta = build_meta(cell(row, "Family"), cell(row, "Common_name"))
                entities[(doi, "SPECIES", species)] = {"freq": 1, "meta": meta}
                paper_dedup[doi].add(("SPECIES", species))

            part = cell(row, "Plant_part")
            if part and ("PLANT PART", part) not in paper_dedup[doi]:
                entities[(doi, "PLANT PART", part)] = {"freq": 1, "meta": None}
                paper_dedup[doi].add(("PLANT PART", part))

            technique = cell(row, "Identification_method")
            if technique and ("ANALYTICAL TECHNIQUE", technique) not in paper_dedup[doi]:
                entities[(doi, "ANALYTICAL TECHNIQUE", technique)] = {"freq": 1, "meta": None}
                paper_dedup[doi].add(("ANALYTICAL TECHNIQUE", technique))

            extraction = cell(row, "Extraction_method")
            if extraction and ("EXTRACTION METHOD", extraction) not in paper_dedup[doi]:
                entities[(doi, "EXTRACTION METHOD", extraction)] = {"freq": 1, "meta": None}
                paper_dedup[doi].add(("EXTRACTION METHOD", extraction))

            # LOCATION — combine non-empty geographic fields
            loc_parts = list(
                filter(
                    None,
                    [
                        cell(row, "Location"),
                        cell(row, "City"),
                        cell(row, "State_State_equivalent"),
                        cell(row, "Country"),
                    ],
                )
            )
            if loc_parts:
                loc_text = ", ".join(loc_parts)
                if ("LOCATION", loc_text) not in paper_dedup[doi]:
                    loc_meta = json.dumps({"country": loc_parts[-1]})
                    entities[(doi, "LOCATION", loc_text)] = {"freq": 1, "meta": loc_meta}
                    paper_dedup[doi].add(("LOCATION", loc_text))

            # DEVELOPMENT STAGE (includes Season_month)
            stage_parts = list(
                filter(
                    None,
                    [
                        cell(row, "Development_stage"),
                        cell(row, "Season_month"),
                    ],
                )
            )
            if stage_parts:
                stage_text = "; ".join(stage_parts)
                if ("DEVELOPMENT STAGE", stage_text) not in paper_dedup[doi]:
                    entities[(doi, "DEVELOPMENT STAGE", stage_text)] = {"freq": 1, "meta": None}
                    paper_dedup[doi].add(("DEVELOPMENT STAGE", stage_text))

    # Group extracted entities by DOI to prepare batched insertion.
    entities_by_doi = {}
    for (doi, label, text), edata in entities.items():
        entities_by_doi.setdefault(doi, []).append((label, text, edata["freq"], edata["meta"]))

    total_entities = len(entities)
    print(f"  Parsed: {len(papers)} papers, {total_entities} entities")

    # Phase 2: Insert normalized records into SQLite.
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT doi FROM papers")
    existing_dois = {r[0] for r in cur.fetchall()}

    new_papers = 0
    inserted_entities = 0
    batch = []

    for doi, paper in papers.items():
        if doi in existing_dois:
            cur.execute("SELECT id FROM papers WHERE doi = ?", (doi,))
            row = cur.fetchone()
            if not row:
                continue
            paper_id = row[0]
        else:
            cur.execute(
                "INSERT OR IGNORE INTO papers "
                "(doi, title, journal, year, is_open_access) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    paper["doi"],
                    paper["title"],
                    paper["journal"],
                    paper["year"],
                    paper["is_open_access"],
                ),
            )
            if cur.lastrowid:
                paper_id = cur.lastrowid
                new_papers += 1
                existing_dois.add(doi)
            else:
                cur.execute("SELECT id FROM papers WHERE doi = ?", (doi,))
                row = cur.fetchone()
                if not row:
                    continue
                paper_id = row[0]
                existing_dois.add(doi)

        # Queue grouped entities for chunked batch insertion.
        for label, text, freq, meta in entities_by_doi.get(doi, []):
            batch.append((paper_id, label, text, freq, meta))
            if len(batch) >= 500:
                cur.executemany(
                    "INSERT OR IGNORE INTO paper_entities "
                    "(paper_id, label, canonical_text, frequency, metadata) "
                    "VALUES (?, ?, ?, ?, ?)",
                    batch,
                )
                # rowcount reflects newly inserted rows; ignored rows
                # do not increment the count.
                inserted_entities += cur.rowcount
                conn.commit()
                batch = []

    if batch:
        cur.executemany(
            "INSERT OR IGNORE INTO paper_entities "
            "(paper_id, label, canonical_text, frequency, metadata) "
            "VALUES (?, ?, ?, ?, ?)",
            batch,
        )
        inserted_entities += cur.rowcount
        conn.commit()

    # Phase 3: Recalculate entity_count totals for affected papers.
    print("  Updating entity counts...")
    for doi in papers:
        cur.execute("SELECT id FROM papers WHERE doi = ?", (doi,))
        row = cur.fetchone()
        if row:
            cur.execute(
                "SELECT COUNT(*) FROM paper_entities WHERE paper_id = ?",
                (row[0],),
            )
            cnt = cur.fetchone()[0]
            cur.execute(
                "UPDATE papers SET entity_count = ? WHERE id = ?",
                (cnt, row[0]),
            )

    conn.commit()
    conn.close()

    print(
        f"\nDone. {new_papers} new papers, "
        f"{inserted_entities} entities inserted of {total_entities}."
    )


if __name__ == "__main__":
    main()
