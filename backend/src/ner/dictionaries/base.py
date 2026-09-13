#!/usr/bin/env python3
"""Build script for compiling dictionary-based NER matchers.

Compiles entity dictionary CSV files into cached binary pickle files for
fast startup and efficient phrase matching.
"""

import csv
import pickle
import time
from pathlib import Path

from backend.src.ner.dictionaries.chemical import (
    CACHE_VERSION,
    build_chemical_cache_data,
)

DATA_DIR = Path(__file__).parent / "data"
BUILD_DIR = Path(__file__).parent / "build"

CATEGORIES = {
    "PLANT PART": "plant_part.csv",
    "ANALYTICAL TECHNIQUE": "analytical_technique.csv",
    "EXTRACTION METHOD": "extraction_method.csv",
    "DEVELOPMENT STAGE": "development_stage.csv",
    "SEASON": "season.csv",
    "SPECIES": "species.csv",
    "CHEMICAL": "chemical.csv",
}


def build_matcher(category: str, csv_file: Path) -> dict:
    """Compile term patterns and canonical mappings for an entity category.

    Routes species and chemical datasets to specialized builders, while
    handling generic delimiter-separated term/alias lists for other
    categories.
    """
    print(f"\n[{category}] Loading terms...")

    if category == "SPECIES":
        return build_species_matcher(csv_file)

    if category == "CHEMICAL":
        return build_chemical_matcher(csv_file)

    terms = []
    canonical_map = {}

    with open(csv_file, encoding="utf-8", errors="ignore") as f:
        reader = csv.reader(f)
        headers = next(reader)

        # Detect presence of synonym or alias columns in CSV header.
        has_aliases = (
            headers
            and len(headers) > 1
            and ("alias" in headers[1].lower() or "synonym" in headers[1].lower())
        )

        for row in reader:
            if not row or not row[0].strip():
                continue
            if row[0].strip().startswith("#"):
                continue

            term = row[0].strip().lower()
            terms.append(term)
            # Map canonical term to itself.
            canonical_map[term] = term

            if has_aliases and len(row) > 1 and row[1].strip():
                for alias in row[1].strip().split("|"):
                    alias_clean = alias.strip().lower()
                    if alias_clean:
                        terms.append(alias_clean)
                        canonical_map[alias_clean] = term

    print(f"[{category}] {len(terms)} terms (with aliases)")
    return {
        "category": category,
        "terms": terms,
        "count": len(terms),
        "canonical_map": canonical_map,
    }


def build_species_matcher(csv_file: Path) -> dict:
    """Build species matcher cache with scientific names and metadata.

    Parses taxonomy columns, generates genus abbreviations (e.g.
    'A. annua'), indexes verified scientific and common names, and maps
    surface aliases to canonical species names.
    """
    terms = []
    canonical_map = {}
    metadata_map = {}
    aliases_by_canonical = {}

    with open(csv_file, encoding="utf-8", errors="ignore", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            scientific_name_input = (
                row.get("scientific_name")
                or row.get("scientific_name_input")
                or row.get("scientific_name_verified")
                or ""
            ).strip()
            scientific_name_verified = (
                row.get("scientific_name_verified")
                or row.get("scientific_name")
                or scientific_name_input
            ).strip()
            accepted_scientific_name = scientific_name_verified or scientific_name_input
            common_name = (row.get("common_name") or "").strip()
            source_db = (row.get("source_db") or "").strip()
            source_url = (row.get("source_url") or "").strip()
            taxon_id = (row.get("taxon_id") or "").strip()
            match_status = (row.get("match_status") or "exact").strip()
            review_required = (row.get("review_required") or "no").strip()

            canonical = (
                accepted_scientific_name or scientific_name_verified or scientific_name_input
            )
            if not canonical:
                continue

            scientific_aliases = [
                value
                for value in [
                    scientific_name_input,
                    scientific_name_verified,
                    accepted_scientific_name,
                ]
                if value
            ]
            scientific_aliases = list(dict.fromkeys(scientific_aliases))
            all_aliases = scientific_aliases + ([common_name] if common_name else [])
            all_aliases = list(dict.fromkeys(all_aliases))

            aliases_by_canonical[canonical] = all_aliases

            base_metadata = {
                "canonical": canonical,
                "accepted_scientific_name": (accepted_scientific_name or canonical),
                "scientific_name_verified": (scientific_name_verified or canonical),
                "common_name": common_name,
                "source_db": source_db,
                "source_url": source_url,
                "taxon_id": taxon_id,
                "match_status": match_status,
                "review_required": review_required,
                "aliases": all_aliases,
            }

            for alias in scientific_aliases:
                alias_lower = alias.lower()
                terms.append(alias_lower)
                canonical_map[alias_lower] = canonical
                metadata_map[alias_lower] = {
                    **base_metadata,
                    "name_type": "scientific",
                }

            # Generate abbreviated genus forms: "A. annua" and
            # "A.annua" to catch common literature shorthand.
            parts = canonical.split()

            if len(parts) == 2 and len(parts[0]) > 1:
                genus_initial = parts[0][0] + "."
                species_epithet = parts[1]
                abbrev_spaced = (genus_initial + " " + species_epithet).lower()
                abbrev_nospace = (genus_initial + species_epithet).lower()
                for abbrev in (abbrev_spaced, abbrev_nospace):
                    if abbrev not in terms:
                        terms.append(abbrev)
                        canonical_map[abbrev] = canonical
                        metadata_map[abbrev] = {
                            **base_metadata,
                            "name_type": "scientific",
                        }

            if common_name:
                common_lower = common_name.lower()
                canonical_map[common_lower] = canonical
                metadata_map[common_lower] = {
                    **base_metadata,
                    "name_type": "common",
                }

    terms = list(dict.fromkeys(terms))
    print(f"[SPECIES] {len(terms)} scientific-name terms")
    return {
        "category": "SPECIES",
        "terms": terms,
        "count": len(terms),
        "canonical_map": canonical_map,
        "metadata_map": metadata_map,
        "aliases_by_canonical": aliases_by_canonical,
    }


def build_chemical_matcher(csv_file: Path) -> dict:
    """Build chemical matcher cache with synonym patterns and metadata."""
    with open(csv_file, encoding="utf-8", errors="ignore", newline="") as f:
        rows = list(csv.DictReader(f))

    chemical_cache = build_chemical_cache_data(
        rows,
        log_collision=lambda alias, existing, canonical: print(
            f"[CHEMICAL] Duplicate alias collision for '{alias}': "
            f"'{existing}' vs '{canonical}'. Keeping neither."
        ),
    )

    print(f"[CHEMICAL] {len(chemical_cache['terms'])} name/synonym terms")
    return {
        "category": "CHEMICAL",
        "terms": chemical_cache["terms"],
        "count": len(chemical_cache["terms"]),
        "canonical_map": chemical_cache["canonical_map"],
        "metadata_map": chemical_cache["metadata_map"],
        "aliases_by_canonical": chemical_cache["aliases_by_canonical"],
        "cache_version": CACHE_VERSION,
        "source_mtime": csv_file.stat().st_mtime,
    }


def main():
    """Build and save binary pattern caches for all entity categories."""
    start_time = time.time()

    print("=" * 60)
    print("Dictionary NER Matcher Builder")
    print("=" * 60)

    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    results = {}
    for category, filename in CATEGORIES.items():
        csv_path = DATA_DIR / filename
        if not csv_path.exists():
            print(f"[WARNING] {filename} not found at {csv_path}, skipping...")
            continue

        result = build_matcher(category, csv_path)
        results[category] = result

    # Save each category to its own binary cache file.
    for category, result in results.items():
        category_slug = category.lower().replace(" ", "_")
        cache_file = BUILD_DIR / f"{category_slug}_cache.pkl"

        cache = {category: result}
        with open(cache_file, "wb") as f:
            pickle.dump(cache, f)

        print(f"[{category}] Cache saved to: {cache_file}")

    elapsed = time.time() - start_time
    total_terms = sum(r["count"] for r in results.values())

    print("=" * 60)
    print(f"Done. {total_terms} terms compiled")
    print(f"Time: {elapsed:.1f}s")
    print("=" * 60)


if __name__ == "__main__":
    main()
