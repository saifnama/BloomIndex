"""Botanical species entity extraction using dictionary matching.

Matches verified scientific names and binomial abbreviations using
spaCy PhraseMatcher, enriching matches with taxonomic metadata.
"""

import csv
import logging
import pickle
from pathlib import Path
from typing import Any

import spacy
from spacy.matcher import PhraseMatcher

logger = logging.getLogger(__name__)

# Canonical entity label for extracted plant species.
ENTITY_TYPE = "SPECIES"

# Vocabulary source and serialized matcher cache file paths.
DATA_DIR = Path(__file__).parent / "data"
BUILD_DIR = Path(__file__).parent / "build"
DATA_FILE = DATA_DIR / "species.csv"
CACHE_FILE = BUILD_DIR / "species_cache.pkl"


class SpeciesMatcher:
    """Extracts botanical species entities using verified nomenclature.

    Maintains a phrase matcher over canonical binomial names, common
    names, and standard abbreviations (e.g. 'A. annua') to enrich
    entities with taxonomic authority metadata.
    """

    def __init__(self, nlp: Any = None):
        """Initialize the species matcher.

        Args:
            nlp: Optional spaCy language model for tokenization. A blank
                English model is instantiated by default.
        """
        self.nlp = nlp or spacy.blank("en")
        self.matcher: PhraseMatcher | None = None
        # Maps surface text variants to their canonical scientific name.
        self.canonical_map: dict[str, str] = {}
        # Stores taxonomic metadata keyed by lowercased variant.
        self.metadata_map: dict[str, dict[str, Any]] = {}
        # Stores all synonyms and vernacular names per canonical name.
        self.aliases_by_canonical: dict[str, list[str]] = {}
        self._load_or_build()

    def _load_or_build(self) -> None:
        """Load compiled patterns from disk cache or rebuild from CSV.

        Rebuilds matcher if the cache file is absent or if the source
        CSV modification timestamp has changed.
        """
        if CACHE_FILE.exists() and DATA_FILE.exists():
            try:
                csv_mtime = DATA_FILE.stat().st_mtime
                with open(CACHE_FILE, "rb") as f:
                    cache = pickle.load(f)

                if ENTITY_TYPE in cache:
                    data = cache[ENTITY_TYPE]
                    if "source_mtime" not in data or data["source_mtime"] != csv_mtime:
                        logger.info("[SpeciesMatcher] CSV changed; rebuilding")
                        self._build_from_csv()
                        return

                    terms = data["terms"]
                    self.canonical_map = data.get("canonical_map", {})
                    self.metadata_map = data.get("metadata_map", {})
                    self.aliases_by_canonical = data.get("aliases_by_canonical", {})

                    matcher = PhraseMatcher(self.nlp.vocab, attr="LOWER")
                    matcher.add(ENTITY_TYPE, [self.nlp.make_doc(t) for t in terms])
                    self.matcher = matcher
                    logger.info("[SpeciesMatcher] Loaded %s patterns from cache", len(terms))
                    return
            except Exception as e:
                logger.warning("Species cache load failed: %s", e)

        # Compile raw vocabulary when cache is stale or missing.
        self._build_from_csv()

    def _build_from_csv(self) -> None:
        """Parse species vocabulary, generate abbreviations, and cache.

        Reads curated taxonomic records, normalizes scientific and
        vernacular names, generates binomial abbreviations, and compiles
        the phrase matcher.
        """
        if not DATA_FILE.exists():
            raise FileNotFoundError(f"Species dictionary not found: {DATA_FILE}")

        BUILD_DIR.mkdir(parents=True, exist_ok=True)

        scientific_terms: set[str] = set()
        metadata_map: dict[str, dict[str, Any]] = {}
        canonical_map: dict[str, str] = {}
        aliases_by_canonical: dict[str, list[str]] = {}

        with open(DATA_FILE, encoding="utf-8", errors="ignore", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Support variant column headers across data releases.
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
                    "accepted_scientific_name": accepted_scientific_name or canonical,
                    "scientific_name_verified": scientific_name_verified or canonical,
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
                    scientific_terms.add(alias_lower)
                    canonical_map[alias_lower] = canonical
                    metadata_map[alias_lower] = {
                        **base_metadata,
                        "name_type": "scientific",
                    }

                # Index abbreviated binomials with and without space.
                parts = canonical.split()
                if len(parts) == 2 and len(parts[0]) > 1:
                    genus_initial = parts[0][0] + "."
                    species_epithet = parts[1]
                    abbrev_spaced = (genus_initial + " " + species_epithet).lower()
                    abbrev_nospace = (genus_initial + species_epithet).lower()
                    for abbrev in (abbrev_spaced, abbrev_nospace):
                        if abbrev not in scientific_terms:
                            scientific_terms.add(abbrev)
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

        self.canonical_map = canonical_map
        self.metadata_map = metadata_map
        self.aliases_by_canonical = aliases_by_canonical

        patterns = [self.nlp.make_doc(term) for term in sorted(scientific_terms)]
        matcher = PhraseMatcher(self.nlp.vocab, attr="LOWER")
        matcher.add(ENTITY_TYPE, patterns)
        self.matcher = matcher

        cache = {
            ENTITY_TYPE: {
                "terms": sorted(scientific_terms),
                "canonical_map": canonical_map,
                "metadata_map": metadata_map,
                "aliases_by_canonical": aliases_by_canonical,
                "source_mtime": DATA_FILE.stat().st_mtime,
            }
        }
        with open(CACHE_FILE, "wb") as f:
            pickle.dump(cache, f)

        logger.info("[SpeciesMatcher] Built %s scientific-name patterns", len(patterns))

    def lookup(self, text: str) -> dict[str, Any] | None:
        """Retrieve taxonomic metadata for a matched species surface form.

        Args:
            text: Lowercased or raw surface form of the species.

        Returns:
            Taxonomic authority metadata dictionary, or None if the term
            is not registered.
        """
        lookup = self.metadata_map.get(text.lower().strip())
        if not lookup:
            return None

        return {
            "text": text,
            "span": text,
            "canonical": lookup.get("accepted_scientific_name") or lookup.get("canonical") or text,
            "aliases": lookup.get("aliases", []),
            "type": ENTITY_TYPE,
            "label": ENTITY_TYPE,
            "score": 1.0,
            "name_type": lookup.get("name_type"),
            "linked_to": None,
            "scientific_name_verified": lookup.get("scientific_name_verified"),
            "accepted_scientific_name": lookup.get("accepted_scientific_name"),
            "common_name": lookup.get("common_name"),
            "source_db": lookup.get("source_db"),
            "source_url": lookup.get("source_url"),
            "taxon_id": lookup.get("taxon_id"),
            "match_status": lookup.get("match_status"),
            "review_required": lookup.get("review_required"),
        }

    def match(self, text: str) -> list[dict[str, Any]]:
        """Extract species entities from text and attach taxonomic data.

        Args:
            text: Input document text to scan for botanical species.

        Returns:
            List of dictionaries containing matched offsets, spans,
            canonical forms, and taxonomic metadata.
        """
        if not text or not text.strip() or self.matcher is None:
            return []

        doc = self.nlp(text)
        entities: list[dict[str, Any]] = []
        seen = set()

        for _, start, end in self.matcher(doc):
            span = doc[start:end]
            key = (span.start_char, span.end_char)
            if key in seen:
                continue
            seen.add(key)

            enriched = self.lookup(span.text)
            if not enriched:
                continue

            enriched.update(
                {
                    "text": span.text,
                    "span": span.text,
                    "start": span.start_char,
                    "end": span.end_char,
                    "name_type": "scientific",
                }
            )
            entities.append(enriched)

        return entities


# Process-level cached singleton matcher instance.
_matcher: SpeciesMatcher | None = None


def get_matcher() -> SpeciesMatcher:
    """Retrieve or initialize the global SpeciesMatcher singleton.

    Returns:
        The initialized SpeciesMatcher instance.
    """
    global _matcher
    if _matcher is None:
        _matcher = SpeciesMatcher()
    return _matcher


def match_species(text: str) -> list[dict[str, Any]]:
    """Scan text for species mentions using the singleton matcher.

    Args:
        text: Input document text to analyse.

    Returns:
        List of matched species entity records.
    """
    return get_matcher().match(text)
