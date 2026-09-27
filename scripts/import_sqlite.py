"""Sync CSV/Excel entity rows into the SQLite database.

One row per input record becomes per-paper entity mentions.
Keys are canonicalized so spelling variants share one row and
repeat mentions accumulate as frequency. Re-importing identical
content is a no-op via the import log.

Usage:
    python scripts/import_sqlite.py -i data.csv -d bloomindex.sqlite
    python scripts/import_sqlite.py -i data.xlsx -d bloomindex.sqlite
    python scripts/import_sqlite.py -i data.csv -d db.sqlite --force
"""

import argparse
import csv
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
from pathlib import Path


def cell(row, *keys):
    """First non-blank value across alias keys.

    Header lookup ignores case, spaces, and underscores so
    equivalent spellings resolve to one column. A value is
    blank when empty, native NaN, or exactly na in any case.
    """
    lowered = {}
    for rk, rv in row.items():
        nk = str(rk).strip().lower().replace(" ", "_")
        if nk not in lowered:
            lowered[nk] = rv
    for k in keys:
        nk = str(k).strip().lower().replace(" ", "_")
        v = lowered.get(nk, "")
        if v is None:
            continue
        if isinstance(v, float) and math.isnan(v):
            continue
        s = str(v).strip()
        if not s:
            continue
        if _is_placeholder(s):
            continue
        return s
    return ""


def _is_placeholder(s):
    """Whether a whole value is the na placeholder.

    Case-insensitive exact match only, so larger strings
    containing those letters still import normally.
    """
    return s.strip().lower() == "na"


def _squash_spaces(text):
    """Single-space text so spacing variants share one key."""
    return re.sub(r"\s+", " ", text.strip())


def canonical_for(label, text):
    """Canonical key for an entity mention.

    Uniform keys keep variant spellings on one row. Species
    uses binomial casing, locations use title casing, and all
    other labels use lowercase.
    """
    text = _squash_spaces(text)
    if not text:
        return ""
    if label == "SPECIES":
        parts = text.split(" ")
        return " ".join(
            [parts[0].capitalize()] + [p.lower() for p in parts[1:]]
        )
    if label == "LOCATION":
        return text.title()
    return text.lower()


def strip_doi_prefix(raw):
    """DOI key with transport wrapper removed.

    The https, http, and doi schemes name a location rather
    than the work, so mixed sources would fork one paper into
    twins. Stripping only the wrapper keeps case-sensitive
    identity intact for the remainder.
    """
    s = (raw or "").strip()
    if not s:
        return ""
    low = s.lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if low.startswith(prefix):
            return s[len(prefix):].strip()
    return s


def to_int(v):
    """Integer value or None when unparseable.

    Whole Excel floats convert directly since years often
    arrive as 2020.0 rather than text.
    """
    try:
        if isinstance(v, float) and v.is_integer():
            return int(v)
        return int(str(v).strip())
    except (ValueError, TypeError, AttributeError):
        return None


def load_rows(input_path):
    """Row dicts from a CSV or Excel file.

    Pandas stays a local import because Excel support is
    optional; CSV-only environments must still run.
    """
    suffix = Path(input_path).suffix.lower()
    if suffix in (".xlsx", ".xls"):
        try:
            import pandas as pd
        except ImportError:
            print(
                "Error: Excel input requires pandas+openpyxl: "
                "pip install pandas openpyxl"
            )
            sys.exit(1)
        try:
            df = pd.read_excel(input_path, dtype=object)
        except Exception as e:
            print(f"Error: failed to read Excel file: {e}")
            sys.exit(1)
        rows = []
        for _, r in df.iterrows():
            d = {}
            for col in df.columns:
                v = r[col]
                try:
                    is_nan = pd.isna(v)
                except Exception:
                    is_nan = False
                d[str(col)] = "" if is_nan else str(v).strip()
            rows.append(d)
        return rows
    with open(input_path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def build_meta(family, common):
    """Taxonomic metadata JSON, or None when empty.

    Null keeps the column queryable instead of storing
    an empty object.
    """
    obj = {}
    if family:
        obj["family"] = family
    if common:
        obj["common_name"] = common
    return json.dumps(obj) if obj else None


def build_location_meta(country, state=None):
    """Location metadata JSON, or None when empty.

    Country and state stay addressable for geo filters
    while the canonical text holds the single place name.
    """
    obj = {}
    if country:
        obj["country"] = country
    if state:
        obj["state"] = state
    return json.dumps(obj) if obj else None


def file_sha256(path):
    """Hex digest of file bytes.

    Byte leg of the repeat guard; the content digest covers
    re-saves that leave mentions unchanged.
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1048576), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_schema(cur):
    """Tables and indexes for papers, entities, and import log.

    Everything is created idempotently so a fresh database
    file bootstraps on first run. The unique entity index
    is what makes conflict upserts possible.
    """
    cur.execute(
        "CREATE TABLE IF NOT EXISTS papers ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "doi VARCHAR NOT NULL UNIQUE, "
        "title VARCHAR, journal VARCHAR, year INTEGER, "
        "is_open_access BOOLEAN, entity_count INTEGER)"
    )
    cur.execute(
        "CREATE TABLE IF NOT EXISTS paper_entities ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "paper_id INTEGER NOT NULL REFERENCES papers(id) "
        "ON DELETE CASCADE, "
        "label VARCHAR NOT NULL, canonical_text VARCHAR NOT NULL, "
        "frequency INTEGER, metadata JSON, "
        "CHECK (metadata IS NULL OR json_valid(metadata)))"
    )
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_entities_uniq "
        "ON paper_entities(paper_id, label, canonical_text)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS ix_paper_entities_paper_id "
        "ON paper_entities(paper_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS ix_paper_entities_label "
        "ON paper_entities(label)"
    )
    cur.execute(
        "CREATE TABLE IF NOT EXISTS import_log ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "file_sha256 VARCHAR NOT NULL UNIQUE, "
        "content_sha256 VARCHAR, "
        "filename VARCHAR, papers INTEGER, entities INTEGER, "
        "imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    # Older databases predate the content column.
    try:
        cur.execute("ALTER TABLE import_log ADD COLUMN content_sha256 VARCHAR")
    except Exception:
        pass


def conform_spelling(value, known):
    """Stored spelling for a place name, else the value as-is.

    Reuses the database spelling on case-insensitive match so
    sources with different casing share one key. Unknown names
    pass through untouched to preserve source fidelity.
    """
    if not value:
        return value
    return known.get(value.strip().lower(), value)


def load_spelling_maps(cur):
    """Lowercased to stored-spelling maps for country/state.

    Lets mixed-case sources converge on existing keys without
    any hardcoded name list.
    """
    maps = {}
    for key in ("country", "state"):
        try:
            rows = cur.execute(
                "SELECT DISTINCT json_extract(metadata, '$." + key + "') "
                "FROM paper_entities WHERE json_extract(metadata, '$."
                + key
                + "') IS NOT NULL"
            ).fetchall()
        except Exception:
            rows = []
        known = {}
        for (val,) in rows:
            if val:
                known.setdefault(str(val).strip().lower(), val)
        maps[key] = known
    return maps


def conform_location_metas(entities, maps):
    """Align LOCATION metadata spellings with stored keys.

    Mixed-case sources would otherwise fork one country into
    twin rows that charts and filters count separately.
    """
    for (doi, label, text), edata in entities.items():
        if label != "LOCATION" or not edata["meta"]:
            continue
        try:
            meta = json.loads(edata["meta"])
        except Exception:
            continue
        if not isinstance(meta, dict):
            continue
        changed = False
        for key in ("country", "state"):
            if meta.get(key):
                fixed = conform_spelling(meta[key], maps.get(key, {}))
                if fixed != meta[key]:
                    meta[key] = fixed
                    changed = True
        if changed:
            edata["meta"] = json.dumps(meta)


def content_sha256(papers, entities):
    """Digest of parsed mentions, immune to file rewrites.

    Raw bytes shift when Excel is re-saved without edits, so
    byte identity alone would miss repeats. The parsed payload
    only changes when mentions actually change.
    """
    payload = {
        "papers": sorted(papers.items()),
        "entities": sorted(
            (doi, label, text, data["freq"], data["meta"])
            for (doi, label, text), data in entities.items()
        ),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def add_mention(entities, doi, label, raw_text, meta=None):
    """Fold one record into the in-memory mention counts.

    Frequency grows per mention while the first non-null
    metadata wins, mirroring the database upsert below so
    the batch load and conflict rule cannot disagree.
    """
    if not raw_text:
        return
    text = canonical_for(label, raw_text)
    if not text:
        return
    key = (doi, label, text)
    if key not in entities:
        entities[key] = {"freq": 0, "meta": None}
    entities[key]["freq"] += 1
    if entities[key]["meta"] is None and meta:
        entities[key]["meta"] = meta


def main():
    """Parse arguments and run the import pipeline."""
    parser = argparse.ArgumentParser(
        description="Sync NER CSV/Excel data into SQLite database."
    )
    parser.add_argument(
        "-i",
        "--input",
        required=True,
        help="Path to CSV (.csv) or Excel (.xlsx/.xls) file",
    )
    parser.add_argument(
        "-d", "--database", required=True, help="Path to SQLite database"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-import an already imported file",
    )
    args = parser.parse_args()
    input_path = args.input
    db_path = args.database

    if not os.path.exists(input_path):
        print(f"Error: input file not found: {input_path}")
        sys.exit(1)

    # Fail early on unwritable destinations instead of mid-import.
    _parent = os.path.dirname(os.path.abspath(db_path))
    if _parent:
        os.makedirs(_parent, exist_ok=True)

    # Byte identity for the log; content identity decides repeats.
    sha = file_sha256(input_path)

    papers = {}
    entities = {}

    print(f"Reading {input_path}...")
    for row in load_rows(input_path):
            doi = strip_doi_prefix(cell(row, "DOI", "DOI number"))
            if not doi:
                continue

            # First record wins so repeated paper fields stay
            # deterministic regardless of row order.
            if doi not in papers:
                oa_raw = cell(row, "is_open_access", "Open Access")
                papers[doi] = {
                    "doi": doi,
                    "title": cell(row, "fetched_title", "Title"),
                    "journal": cell(row, "journal", "Journal"),
                    "year": to_int(
                        cell(row, "year", "publication_year")
                        or cell(row, "Year of data collection")
                    ),
                    "is_open_access": 1
                    if oa_raw.upper() in ("TRUE", "1", "YES", "OPEN")
                    else 0,
                }

            add_mention(
                entities, doi, "CHEMICAL", cell(row, "Compound_name", "Chemical")
            )

            species_raw = cell(row, "species", "Scientific_name", "Scientific_Name")
            if species_raw:
                meta = build_meta(
                    cell(row, "Family"),
                    cell(row, "Common_name", "Common name"),
                )
                add_mention(entities, doi, "SPECIES", species_raw, meta)

            add_mention(
                entities, doi, "PLANT PART", cell(row, "Plant_part", "Plant Part")
            )
            # Technique cells may list several methods while other
            # cells hold single values: chemicals keep semicolons
            # as nomenclature and locations stay single places.
            for _tech in cell(
                row, "Identification_method", "Analytical Technique"
            ).split(";"):
                add_mention(entities, doi, "ANALYTICAL TECHNIQUE", _tech)
            add_mention(
                entities,
                doi,
                "EXTRACTION METHOD",
                cell(row, "Extraction_method", "Extraction Method"),
            )

            # One place per mention; region context travels in
            # metadata so text filters stay exact.
            loc_text = cell(row, "Location") or cell(row, "Country")
            if loc_text:
                loc_meta = build_location_meta(
                    cell(row, "Country"),
                    cell(
                        row,
                        "State",
                        "State_equivalent",
                        "State_State_equivalent",
                        "City",
                    ),
                )
                add_mention(entities, doi, "LOCATION", loc_text, loc_meta)

            stage_text = cell(row, "Development_stage", "Development stage")
            add_mention(entities, doi, "DEVELOPMENT STAGE", stage_text)

    total_entities = len(entities)
    print(f"  Parsed: {len(papers)} papers, {total_entities} entities")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    ensure_schema(cur)
    conn.commit()

    # Same bytes or same mentions both imply a repeat import that
    # would only double-count. Bytes shift on plain Excel re-saves,
    # so content identity is the reliable signal. Force bypasses.
    csha = content_sha256(papers, entities)
    try:
        seen = cur.execute(
            "SELECT id FROM import_log WHERE file_sha256=? OR content_sha256=?",
            (sha, csha),
        ).fetchone()
    except Exception:
        seen = cur.execute(
            "SELECT id FROM import_log WHERE file_sha256=?", (sha,)
        ).fetchone()
    if seen and not args.force:
        print(
            f"Skipped: this content was already imported "
            f"(sha256 {csha[:12]}...). Use --force to re-import."
        )
        conn.close()
        return

    # Converge place-name spellings on stored keys so mixed-case
    # sources share country rows instead of forking twins. Grouping
    # follows so batches carry conformed metadata.
    conform_location_metas(entities, load_spelling_maps(cur))
    entities_by_doi = {}
    for (doi, label, text), edata in entities.items():
        entities_by_doi.setdefault(doi, []).append(
            (label, text, edata["freq"], edata["meta"])
        )

    cur.execute("SELECT doi FROM papers")
    existing_dois = {r[0] for r in cur.fetchall()}

    new_papers = 0
    upserted_entities = 0
    batch = []

    UPSERT_SQL = (
        "INSERT INTO paper_entities "
        "(paper_id, label, canonical_text, frequency, metadata) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(paper_id, label, canonical_text) DO UPDATE SET "
        "frequency = paper_entities.frequency + excluded.frequency, "
        "metadata = COALESCE(paper_entities.metadata, excluded.metadata)"
    )

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

        for label, text, freq, meta in entities_by_doi.get(doi, []):
            batch.append((paper_id, label, text, freq, meta))
            if len(batch) >= 500:
                cur.executemany(UPSERT_SQL, batch)
                upserted_entities += len(batch)
                conn.commit()
                batch = []

    if batch:
        cur.executemany(UPSERT_SQL, batch)
        upserted_entities += len(batch)
        conn.commit()

    # Denormalized counts must follow the entity table.
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

    cur.execute(
        "INSERT OR IGNORE INTO import_log "
        "(file_sha256, content_sha256, filename, papers, entities) "
        "VALUES (?, ?, ?, ?, ?)",
        (sha, csha, os.path.basename(input_path), len(papers), total_entities),
    )
    conn.commit()
    conn.close()

    print(
        f"\nDone. {new_papers} new papers, "
        f"{upserted_entities} entity mentions upserted across "
        f"{total_entities} distinct keys."
    )


if __name__ == "__main__":
    main()
