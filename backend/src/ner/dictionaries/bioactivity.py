"""Dictionary-based bioactivity matcher using spaCy PhraseMatcher.

Provides fast phrase lookup for pharmacological and bioactivity terms
(such as antimicrobial, antioxidant, and cytotoxic) against curated
vocabularies.
"""

import csv
import logging
import pickle
from pathlib import Path
from typing import Any

import spacy
from spacy.matcher import PhraseMatcher

logger = logging.getLogger(__name__)

ENTITY_TYPE = "BIOACTIVITY"

DATA_DIR = Path(__file__).parent / "data"
BUILD_DIR = Path(__file__).parent / "build"
CACHE_FILE = BUILD_DIR / "bioactivity_cache.pkl"


class BioactivityMatcher:
    """Dictionary matcher for pharmacological and bioactivity terms."""

    def __init__(self, nlp: Any = None):
        """Initialize matcher with a blank model or provided NLP pipeline."""
        self.nlp = nlp or spacy.blank("en")
        self.matcher = None
        # Maps lowercase aliases and synonyms to canonical terms.
        self.canonical_map = {}

        self._load_or_build()

    def _load_or_build(self):
        """Load pattern structures from disk cache or rebuild from CSV."""
        if CACHE_FILE.exists():
            try:
                with open(CACHE_FILE, "rb") as f:
                    cache = pickle.load(f)

                if ENTITY_TYPE in cache:
                    data = cache[ENTITY_TYPE]
                    terms = data["terms"]
                    canonical_map = data.get("canonical_map", {})

                    patterns = [self.nlp.make_doc(t) for t in terms]
                    self.matcher = PhraseMatcher(self.nlp.vocab, attr="LOWER")
                    self.matcher.add(ENTITY_TYPE, patterns)

                    self.canonical_map = canonical_map

                    logger.info(f"[BioactivityMatcher] Loaded {len(patterns)} patterns from cache")
                    return
            except Exception as e:
                logger.warning(f"Cache load failed: {e}")

        self._build_from_csv()

    def _build_from_csv(self):
        """Build phrase patterns from CSV vocabulary with synonyms."""
        csv_path = DATA_DIR / "bioactivity.csv"

        terms = []
        with open(csv_path, encoding="utf-8") as f:
            reader = csv.reader(f)
            headers = next(reader, [])

            # Check if second column contains synonym variations.
            has_synonyms = headers and len(headers) > 1 and "synonym" in headers[1].lower()

            for row in reader:
                if not row or not row[0].strip():
                    continue

                if row[0].strip().startswith("#"):
                    continue

                terms.append(row[0].strip().lower())

                if has_synonyms and len(row) > 1 and row[1].strip():
                    for synonym in row[1].strip().split("|"):
                        if synonym.strip():
                            terms.append(synonym.strip().lower())

        terms = list(set(terms))

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
                    for synonym in row[1].strip().split("|"):
                        if synonym.strip():
                            canonical_map[synonym.strip().lower()] = primary

        self.canonical_map = canonical_map

        self.matcher = PhraseMatcher(self.nlp.vocab, attr="LOWER")
        patterns = [self.nlp.make_doc(t) for t in terms if t]
        self.matcher.add(ENTITY_TYPE, patterns)

        logger.info(f"[BioactivityMatcher] Built {len(patterns)} patterns from CSV (with synonyms)")

    def get_synonyms_for_canonical(self, canonical: str) -> list[str]:
        """Return all synonym variations mapped to a canonical term."""
        synonyms = []
        for variation, can in self.canonical_map.items():
            if can == canonical:
                synonyms.append(variation)
        return synonyms

    def match(self, text: str) -> list[dict[str, Any]]:
        """Extract bioactivity entity mentions from input text.

        Scans text using phrase matching and maps recognized terms to
        canonical forms alongside their known synonyms.
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

            # Resolve canonical term for case-insensitive lookup.
            canonical = self.canonical_map.get(span.text.lower(), span.text)
            synonyms = self.get_synonyms_for_canonical(canonical)

            entities.append(
                {
                    "span": span.text,
                    "canonical": canonical,
                    "synonyms": synonyms,
                    "type": ENTITY_TYPE,
                    "start": span.start_char,
                    "end": span.end_char,
                    "name_type": None,
                    "linked_to": None,
                    "label": ENTITY_TYPE,
                    "score": 1.0,
                }
            )

        return entities


# Global singleton instance cache.
_matcher: BioactivityMatcher | None = None


def get_matcher() -> BioactivityMatcher:
    """Return singleton matcher instance, creating it on first access."""
    global _matcher
    if _matcher is None:
        _matcher = BioactivityMatcher()
    return _matcher


def match_bioactivities(text: str) -> list[dict[str, Any]]:
    """Extract bioactivity entities from input text via singleton matcher."""
    return get_matcher().match(text)


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO)

    # Test
    test_text = """
    The extract showed antimicrobial and antioxidant activities.
    It exhibited antifungal properties and anti-inflammatory effects.
    The compound has cytotoxic activity against cancer cells.
    """

    entities = match_bioactivities(test_text)
    print(f"\nFound {len(entities)} bioactivity terms:")
    for e in entities:
        print(f"  - '{e['span']}' ({e['start']}-{e['end']})")
