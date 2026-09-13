"""Dictionary-based analytical technique matcher using spaCy PhraseMatcher.

Provides fast lookup for analytical and identification techniques
(such as GC-MS, NMR, and HPLC) using a blank spaCy English model.
"""

import csv
import logging
import pickle
import re
from pathlib import Path
from typing import Any

import spacy
from spacy.matcher import PhraseMatcher

logger = logging.getLogger(__name__)

ENTITY_TYPE = "ANALYTICAL TECHNIQUE"

DATA_DIR = Path(__file__).parent / "data"
BUILD_DIR = Path(__file__).parent / "build"
CACHE_FILE = BUILD_DIR / "analytical_technique_cache.pkl"
DATA_FILE = DATA_DIR / "analytical_technique.csv"


def _normalize_separators(text: str) -> str:
    """Normalize unicode dashes and slashes to standard hyphens."""
    text = re.sub(r"[\u2013\u2014\u2015\u2212]", "-", text)
    return text.replace("/", "-")


class AnalyticalTechniqueMatcher:
    """Dictionary matcher for analytical techniques using PhraseMatcher."""

    def __init__(self, nlp: Any = None):
        """Initialize the matcher with a blank model or provided NLP pipeline."""
        self.nlp = nlp or spacy.blank("en")
        self.matcher = None
        # Maps lowercased aliases to primary canonical terms.
        self.canonical_map = {}
        self._load_or_build()

    def _load_or_build(self):
        """Load patterns from disk cache or rebuild from CSV if stale."""
        if CACHE_FILE.exists() and DATA_FILE.exists():
            try:
                csv_mtime = DATA_FILE.stat().st_mtime
                with open(CACHE_FILE, "rb") as f:
                    cache = pickle.load(f)

                if ENTITY_TYPE in cache:
                    data = cache[ENTITY_TYPE]
                    # Rebuild if the underlying CSV source file has
                    # been modified since the cache was written.
                    if "source_mtime" not in data or data["source_mtime"] != csv_mtime:
                        logger.info("[AnalyticalTechniqueMatcher] CSV changed; rebuilding")
                        self._build_from_csv()
                        return

                    terms = data["terms"]
                    canonical_map = data.get("canonical_map", {})

                    patterns = [self.nlp.make_doc(t) for t in terms]
                    self.matcher = PhraseMatcher(self.nlp.vocab, attr="LOWER")
                    self.matcher.add(ENTITY_TYPE, patterns)

                    self.canonical_map = canonical_map

                    logger.info(
                        f"[AnalyticalTechniqueMatcher] Loaded {len(patterns)} patterns from cache"
                    )
                    return
            except Exception as e:
                logger.warning(f"Cache load failed: {e}")

        self._build_from_csv()

    def _build_from_csv(self):
        """Build phrase matcher from CSV file with synonym alias support.

        Reads the analytical technique CSV, extracts primary terms and
        delimited synonyms, constructs canonical term mappings, caches
        the compiled structure to disk, and indexes patterns in spaCy.
        """
        csv_path = DATA_DIR / "analytical_technique.csv"

        enc = "utf-8"
        try:
            with open(csv_path, encoding="utf-8") as f:
                f.read()
        except UnicodeDecodeError:
            enc = "cp1252"
        terms = []
        with open(csv_path, encoding=enc) as f:
            reader = csv.reader(f)
            headers = next(reader, [])

            # Check for synonyms column (term,synonyms format).
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
        with open(csv_path, encoding=enc) as f:
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

        # Cache built terms and source file mtime for staleness checks.
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

        # Normalize unicode dash characters so hyphenated terms match.
        self.matcher = PhraseMatcher(self.nlp.vocab, attr="LOWER")
        patterns = [self.nlp.make_doc(_normalize_separators(t)) for t in terms if t]
        self.matcher.add(ENTITY_TYPE, patterns)

        logger.info(
            f"[AnalyticalTechniqueMatcher] Built {len(patterns)} patterns from CSV (with aliases)"
        )

    def get_aliases_for_canonical(self, canonical: str) -> list[str]:
        """Return all recorded alias variations for a canonical term."""
        aliases = []
        for variation, can in self.canonical_map.items():
            if can == canonical:
                aliases.append(variation)
        return aliases

    def match(self, text: str) -> list[dict[str, Any]]:
        """Extract analytical technique entity mentions from input text.

        Finds occurrences using phrase matching, maps each match to its
        canonical term and registered aliases, and filters out shorter
        nested spans contained within longer matches.
        """
        if not text or not text.strip():
            return []

        doc = self.nlp(_normalize_separators(text))
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
                    "score": 1.0,
                }
            )

        # Remove shorter nested entities at overlapping offsets.
        entities.sort(key=lambda e: (e["start"], -(e["end"] - e["start"])))
        deduped = []
        for ent in entities:
            if not any(
                ent["start"] >= other["start"]
                and ent["end"] <= other["end"]
                and (ent["end"] - ent["start"]) < (other["end"] - other["start"])
                for other in deduped
            ):
                deduped.append(ent)

        return deduped


# Global singleton instance cache.
_matcher: AnalyticalTechniqueMatcher | None = None


def get_matcher() -> AnalyticalTechniqueMatcher:
    """Return singleton matcher instance, creating it on first access."""
    global _matcher
    if _matcher is None:
        _matcher = AnalyticalTechniqueMatcher()
    return _matcher


def match_analytical_techniques(text: str) -> list[dict[str, Any]]:
    """Extract analytical technique entities from input text via singleton."""
    return get_matcher().match(text)


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO)

    # Test
    test_text = """
    The essential oil was analysed by GC-MS and GC-FID.
    NMR spectroscopy was used for structure elucidation.
    HPLC was used for compound isolation.
    TLC was performed for preliminary screening.
    """

    entities = match_analytical_techniques(test_text)
    print(f"\nFound {len(entities)} analytical techniques:")
    for e in entities:
        print(f"  - '{e['span']}' ({e['start']}-{e['end']})")
