"""Plant extraction method entity extraction using dictionary matching.

Matches phytochemical extraction procedures and protocols using spaCy
PhraseMatcher against curated vocabulary files with canonical mappings.
"""

import csv
import logging
import pickle
from pathlib import Path
from typing import Any

import spacy
from spacy.matcher import PhraseMatcher

logger = logging.getLogger(__name__)

# Canonical entity label for extracted extraction methods.
ENTITY_TYPE = "EXTRACTION METHOD"

# Vocabulary source and serialized matcher cache file paths.
DATA_DIR = Path(__file__).parent / "data"
BUILD_DIR = Path(__file__).parent / "build"
CACHE_FILE = BUILD_DIR / "extraction_method_cache.pkl"
DATA_FILE = DATA_DIR / "extraction_method.csv"


class ExtractionMethodMatcher:
    """Extracts extraction method entities using phrase matching.

    Maintains a dictionary of canonical extraction techniques and
    textual aliases to identify extraction procedures without model
    overhead.
    """

    def __init__(self, nlp: Any = None):
        """Initialize the extraction method matcher.

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
                        logger.info("[ExtractionMethodMatcher] CSV changed; rebuilding")
                        self._build_from_csv()
                        return

                    terms = data["terms"]
                    canonical_map = data.get("canonical_map", {})

                    # Compile lowercased patterns for PhraseMatcher.
                    patterns = [self.nlp.make_doc(t) for t in terms]
                    self.matcher = PhraseMatcher(self.nlp.vocab, attr="LOWER")
                    self.matcher.add(ENTITY_TYPE, patterns)

                    self.canonical_map = canonical_map

                    logger.info(
                        f"[ExtractionMethodMatcher] Loaded {len(patterns)} patterns from cache"
                    )
                    return
            except Exception as e:
                logger.warning(f"Cache load failed: {e}")

        # Compile raw vocabulary when cache is stale or missing.
        self._build_from_csv()

    def _build_from_csv(self):
        """Parse method vocabulary and compile phrase matching patterns.

        Reads canonical terms and synonyms from CSV, populates mapping
        tables, and caches compiled patterns.
        """
        csv_path = DATA_DIR / "extraction_method.csv"

        terms = []
        with open(csv_path, encoding="utf-8") as f:
            reader = csv.reader(f)
            headers = next(reader, [])

            for row in reader:
                if not row or not row[0].strip():
                    continue

                term = row[0].strip()

                # Ignore commented lines in the vocabulary source file.
                if term.startswith("#"):
                    continue

                terms.append(term.lower())

                # Map terms and variant aliases to canonical label.
                self.canonical_map[term.lower()] = term

                # Register synonyms under the canonical term.
                if len(row) > 1 and row[1].strip():
                    aliases = row[1].strip()
                    for alias in aliases.split("|"):
                        alias = alias.strip()
                        if alias:
                            terms.append(alias.lower())
                            self.canonical_map[alias.lower()] = term

        terms = list(dict.fromkeys(terms))

        # Create phrase matcher with case-insensitive lookup.
        patterns = [self.nlp.make_doc(t) for t in terms]
        self.matcher = PhraseMatcher(self.nlp.vocab, attr="LOWER")
        self.matcher.add(ENTITY_TYPE, patterns)

        # Persist compiled terminology for fast startup in future runs.
        self._save_cache(terms)

        logger.info(
            f"[ExtractionMethodMatcher] Built {len(patterns)} patterns from CSV (with aliases)"
        )

    def _save_cache(self, terms: list[str]):
        """Serialize compiled terms and canonical mappings to disk cache.

        Args:
            terms: List of unique lowercased vocabulary terms to cache.
        """
        BUILD_DIR.mkdir(parents=True, exist_ok=True)

        cache = {
            ENTITY_TYPE: {
                "terms": terms,
                "canonical_map": self.canonical_map,
                "source_mtime": DATA_FILE.stat().st_mtime,
            }
        }

        with open(CACHE_FILE, "wb") as f:
            pickle.dump(cache, f)

        logger.info(f"[ExtractionMethodMatcher] Cache saved to {CACHE_FILE}")

    def get_aliases_for_canonical(self, canonical: str) -> list[str]:
        """Retrieve all registered alias variations for a canonical term.

        Args:
            canonical: The canonical extraction method name.

        Returns:
            List of alias terms mapped to the specified method.
        """
        aliases = []
        for variation, can in self.canonical_map.items():
            if can == canonical:
                aliases.append(variation)
        return aliases

    def match(self, text: str) -> list[dict[str, Any]]:
        """Extract extraction method entities from input text.

        Args:
            text: Input document text to scan for method mentions.

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

            # Resolve matched surface form to its canonical method name.
            canonical = self.canonical_map.get(span.text.lower(), span.text)

            # Retrieve all synonyms for the canonical method.
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
_matcher: ExtractionMethodMatcher | None = None


def get_matcher() -> ExtractionMethodMatcher:
    """Retrieve or initialize global ExtractionMethodMatcher singleton.

    Returns:
        The initialized ExtractionMethodMatcher instance.
    """
    global _matcher
    if _matcher is None:
        _matcher = ExtractionMethodMatcher()
    return _matcher


def match_extraction_methods(text: str) -> list[dict[str, Any]]:
    """Scan text for extraction methods using the singleton matcher.

    Args:
        text: Input document text to analyse.

    Returns:
        List of matched extraction method entity records.
    """
    return get_matcher().match(text)


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO)

    # Verify extraction method detection on sample text.
    test_text = """
    The plant material was extracted using soxhlet extraction with methanol.
    Maceration was performed for 24 hours.
    Ultrasound assisted extraction was also used.
    Supercritical fluid extraction gave better yields.
    """

    entities = match_extraction_methods(test_text)
    for e in entities:
        print(f"  {e['span']} -> {e['canonical']}")
