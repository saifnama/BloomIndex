"""Plant part entity extraction using dictionary matching.

Matches anatomical plant structures (such as leaves, rhizomes, or
roots) using spaCy PhraseMatcher against curated botanical vocabulary.
"""

import csv
import logging
import pickle
from pathlib import Path
from typing import Any

import spacy
from spacy.matcher import PhraseMatcher

logger = logging.getLogger(__name__)

# Vocabulary source and serialized matcher cache file paths.
DATA_DIR = Path(__file__).parent / "data"
BUILD_DIR = Path(__file__).parent / "build"
CACHE_FILE = BUILD_DIR / "plant_part_cache.pkl"
DATA_FILE = DATA_DIR / "plant_part.csv"

# Canonical entity label for extracted plant anatomical parts.
ENTITY_TYPE = "PLANT PART"


class PlantPartMatcher:
    """Extracts plant anatomical part entities using phrase matching.

    Maintains a curated vocabulary of plant organs, tissues, and
    textual synonyms to identify botanical components without
    statistical model overhead.
    """

    def __init__(self, nlp: Any = None):
        """Initialize the plant part matcher.

        Args:
            nlp: Optional spaCy language model for tokenization. A blank
                English model is instantiated by default.
        """
        self.nlp = nlp or spacy.blank("en")
        self.matcher = None
        # Maps lowercased aliases to their canonical term.
        self.canonical_map = {}
        self._load_or_build()

    def _load_or_build(self):
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
                        logger.info("[PlantPartMatcher] CSV changed; rebuilding")
                        self._build_from_csv()
                        return

                    terms = data["terms"]
                    canonical_map = data.get("canonical_map", {})

                    # Compile lowercased patterns for PhraseMatcher.
                    patterns = [self.nlp.make_doc(t) for t in terms]
                    self.matcher = PhraseMatcher(self.nlp.vocab, attr="LOWER")
                    self.matcher.add(ENTITY_TYPE, patterns)

                    self.canonical_map = canonical_map

                    logger.info(f"[PlantPartMatcher] Loaded {len(patterns)} patterns from cache")
                    return
            except Exception as e:
                logger.warning(f"Cache load failed: {e}")

        # Compile raw vocabulary when cache is stale or missing.
        self._build_from_csv()

    def _build_from_csv(self):
        """Parse anatomical vocabulary and compile phrase matching patterns.

        Reads canonical plant parts and synonyms from CSV, constructs
        normalization lookup tables, and caches compiled patterns.
        """
        csv_path = DATA_DIR / "plant_part.csv"

        terms = []
        with open(csv_path, encoding="utf-8") as f:
            reader = csv.reader(f)
            headers = next(reader, [])

            # Support schemas with an optional alias or synonym column.
            second_header = headers[1].lower() if headers and len(headers) > 1 else ""
            has_aliases = "alias" in second_header or "synonym" in second_header

            for row in reader:
                if not row or not row[0].strip():
                    continue

                # Ignore commented lines in the vocabulary source file.
                if row[0].strip().startswith("#"):
                    continue

                # Add primary term.
                terms.append(row[0].strip().lower())

                # Add pipe-delimited synonyms from the secondary column.
                if has_aliases and len(row) > 1 and row[1].strip():
                    for alias in row[1].strip().split("|"):
                        if alias.strip():
                            terms.append(alias.strip().lower())
                elif not has_aliases:
                    terms.append(row[0].strip().lower())

        # Deduplicate terms while maintaining deterministic structure.
        terms = list(set(terms))

        # Map each surface variant to its canonical form.
        canonical_map = {}
        with open(csv_path, encoding="utf-8") as f:
            reader = csv.reader(f)
            headers = next(reader, [])
            for row in reader:
                if not row or not row[0].strip() or row[0].strip().startswith("#"):
                    continue
                primary = row[0].strip().lower()
                canonical_map[primary] = primary
                if len(row) > 1 and row[1].strip():
                    for alias in row[1].strip().split("|"):
                        if alias.strip():
                            canonical_map[alias.strip().lower()] = primary

        self.canonical_map = canonical_map

        # Cache compiled vocabulary alongside source modification time.
        cache_data = {
            ENTITY_TYPE: {
                "terms": terms,
                "canonical_map": canonical_map,
                "source_mtime": DATA_FILE.stat().st_mtime,
            }
        }
        BUILD_DIR.mkdir(parents=True, exist_ok=True)
        with open(CACHE_FILE, "wb") as f:
            pickle.dump(cache_data, f)

        # Create phrase matcher with case-insensitive lookup.
        self.matcher = PhraseMatcher(self.nlp.vocab, attr="LOWER")
        patterns = [self.nlp.make_doc(t) for t in terms if t]
        self.matcher.add(ENTITY_TYPE, patterns)

        logger.info(f"[PlantPartMatcher] Built {len(patterns)} patterns from CSV (with aliases)")

    def get_aliases_for_canonical(self, canonical: str) -> list[str]:
        """Retrieve all registered alias variations for a canonical term.

        Args:
            canonical: The canonical plant part name.

        Returns:
            List of alias terms mapped to the specified plant part.
        """
        aliases = []
        for variation, can in self.canonical_map.items():
            if can == canonical:
                aliases.append(variation)
        return aliases

    def match(self, text: str) -> list[dict[str, Any]]:
        """Extract plant part entities from input text.

        Args:
            text: Input document text to scan for part mentions.

        Returns:
            List of dictionaries containing matched span offsets,
            canonical forms, associated aliases, and entity metadata.
        """
        if not text or not text.strip():
            return []

        doc = self.nlp(text)
        entities = []
        seen = set()

        for match_id, start, end in self.matcher(doc):
            span = doc[start:end]
            key = (span.start_char, span.end_char)

            if key in seen:
                continue
            seen.add(key)

            # Resolve matched surface form to its canonical plant part.
            canonical = self.canonical_map.get(span.text.lower(), span.text)

            # Retrieve all synonyms for the canonical plant part.
            aliases = self.get_aliases_for_canonical(canonical)

            entities.append(
                {
                    "span": span.text,
                    "canonical": canonical,
                    "aliases": aliases,
                    "type": ENTITY_TYPE,
                    "start": span.start_char,
                    "end": span.end_char,
                    "name_type": None,
                    "linked_to": None,
                    "label": ENTITY_TYPE,
                    "score": 1.0,  # Exact dictionary match.
                }
            )

        return entities


# Process-level cached singleton matcher instance.
_matcher: PlantPartMatcher | None = None


def get_matcher() -> PlantPartMatcher:
    """Retrieve or initialize the global PlantPartMatcher singleton.

    Returns:
        The initialized PlantPartMatcher instance.
    """
    global _matcher
    if _matcher is None:
        _matcher = PlantPartMatcher()
    return _matcher


def match_plant_parts(text: str) -> list[dict[str, Any]]:
    """Scan text for plant part mentions using the singleton matcher.

    Args:
        text: Input document text to analyse.

    Returns:
        List of matched plant part entity records.
    """
    return get_matcher().match(text)


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO)

    # Verify plant part extraction on sample botanical text.
    test_text = """
    The leaves and bark of Cinnamomum verum were collected from Kerala.
    Fresh rhizomes and roots were used for extraction.
    Essential oil from flower and stem showed antimicrobial activity.
    """

    entities = match_plant_parts(test_text)
    print(f"\nFound {len(entities)} plant parts:")
    for e in entities:
        print(f"  - '{e['span']}' ({e['start']}-{e['end']})")
