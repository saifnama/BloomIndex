"""Dictionary-based chemical entity matcher using spaCy PhraseMatcher.

Matches preferred chemical names and synonyms with high precision and
enriches extracted chemical entities with SMILES, InChIKey, and formula
metadata from curated reference dictionaries.
"""

import logging
import pickle
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import spacy
from spacy.matcher import PhraseMatcher

logger = logging.getLogger(__name__)

ENTITY_TYPE = "CHEMICAL"

DATA_DIR = Path(__file__).parent / "data"
BUILD_DIR = Path(__file__).parent / "build"
DATA_FILE = DATA_DIR / "chemical.csv"
CACHE_FILE = BUILD_DIR / "chemical_cache.pkl"
CACHE_VERSION = "v9"


GREEK_MAP = {
    "α": "alpha",
    "β": "beta",
    "γ": "gamma",
    "δ": "delta",
    "ε": "epsilon",
    "ζ": "zeta",
    "η": "eta",
    "θ": "theta",
    "ι": "iota",
    "κ": "kappa",
    "λ": "lambda",
    "μ": "mu",
    "ν": "nu",
    "ξ": "xi",
    "ο": "omicron",
    "π": "pi",
    "ρ": "rho",
    "σ": "sigma",
    "τ": "tau",
    "υ": "upsilon",
    "φ": "phi",
    "χ": "chi",
    "ψ": "psi",
    "ω": "omega",
    "ς": "sigma",
    "Α": "alpha",
    "Β": "beta",
    "Γ": "gamma",
    "Δ": "delta",
    "Ε": "epsilon",
    "Ζ": "zeta",
    "Η": "eta",
    "Θ": "theta",
    "Ι": "iota",
    "Κ": "kappa",
    "Λ": "lambda",
    "Μ": "mu",
    "Ν": "nu",
    "Ξ": "xi",
    "Ο": "omicron",
    "Π": "pi",
    "Ρ": "rho",
    "Σ": "sigma",
    "Τ": "tau",
    "Υ": "upsilon",
    "Φ": "phi",
    "Χ": "chi",
    "Ψ": "psi",
    "Ω": "omega",
}


def _normalize_greek(text: str) -> tuple[str, list[int]]:
    """Replace Greek letters with Latin equivalents and return offset map.

    When a Greek letter directly succeeds an alphanumeric character, a
    hyphen is inserted so the expansion matches patterns like
    '6-alpha-ol'. The offset map tracks each normalized character index
    back to the original text offset.
    """
    result: list[str] = []
    offset_map: list[int] = []
    for i, ch in enumerate(text):
        replacement = GREEK_MAP.get(ch)
        if replacement:
            if i > 0 and text[i - 1].isalnum():
                result.append("-")
                offset_map.append(i)
            result.extend(replacement)
            offset_map.extend([i] * len(replacement))
        else:
            result.append(ch)
            offset_map.append(i)
    return "".join(result), offset_map


def _normalize_for_match(text: str) -> str:
    """Normalize text by expanding Greek letters and spacing delimiters."""
    text, _ = _normalize_greek(text)
    return re.sub(
        r"[/>,\u2032\u2019\u0027]",
        lambda m: " " * (m.end() - m.start()),
        text,
    )


# Matches periods trailing letters that spaCy merges as abbreviations.
_ABBREV_SPLIT_RE = re.compile(r"([A-Za-z])\.")


def _split_abbreviations(text: str) -> tuple[str, list[int]]:
    """Separate trailing periods from letters with an offset map.

    Splits pairs like 'D.' into 'D .' to enable multi-token matching
    where spaCy tokenization would otherwise combine the abbreviation.
    """

    offset: list[int] = []
    last = 0
    parts: list[str] = []
    for m in _ABBREV_SPLIT_RE.finditer(text):
        for i in range(last, m.start() + 1):
            offset.append(i)
        parts.append(text[last : m.start() + 1])
        offset.append(m.start() + 1)
        parts.append(" ")
        offset.append(m.start() + 1)
        parts.append(".")
        last = m.end()
    parts.append(text[last:])
    offset.extend(range(last, len(text)))
    return "".join(parts), offset


def _normalize_alias(text: str) -> str:
    """Strip all non-alphanumeric characters for fuzzy alias lookup."""
    return re.sub(r"[^a-z0-9]+", "", text.lower().strip())


def _alias_variants(text: str) -> list[str]:
    """Generate structural text variations for robust chemical term matching."""
    text = text.strip()
    if not text:
        return []

    variants = [text]
    hyphen_to_space = re.sub(r"[-_/]+", " ", text)
    if hyphen_to_space != text:
        variants.append(re.sub(r"\s+", " ", hyphen_to_space).strip())

    punctuation_removed = re.sub(r"[^A-Za-z0-9\s]", "", text)
    punctuation_removed = re.sub(r"\s+", " ", punctuation_removed).strip()
    if punctuation_removed and punctuation_removed not in variants:
        variants.append(punctuation_removed)

    # Strip parentheses while retaining internal hyphens: (E)-X -> E-X.
    parens_stripped = re.sub(r"[()]", "", text)
    if parens_stripped != text and parens_stripped not in variants:
        variants.append(parens_stripped)

    # Strip stereochemical prefix entirely: (E)-nerolidol -> nerolidol.
    stereo_stripped = re.sub(r"^\([A-Z](?:,[A-Z])*\)-", "", text)
    if stereo_stripped != text and stereo_stripped not in variants:
        variants.append(stereo_stripped)

    return list(dict.fromkeys(v for v in variants if v))


def _primary_name(row: dict[str, Any]) -> str:
    """Extract primary chemical name from dictionary row fields."""
    return (row.get("term") or row.get("preferred_name") or "").strip()


def build_chemical_cache_data(
    rows: list[dict[str, Any]],
    log_collision: Callable[[str, str, str], None] | None = None,
) -> dict[str, Any]:
    """Compile chemical terms, alias mappings, and metadata from CSV rows.

    Resolves priority conflicts when an alias maps across multiple
    substance records by preferring primary names and shorter canonical
    names.
    """
    terms: set[str] = set()
    metadata_map: dict[str, dict[str, Any]] = {}
    canonical_map: dict[str, str] = {}
    aliases_by_canonical: dict[str, list[str]] = {}

    alias_candidates: dict[str, list[dict[str, Any]]] = {}

    prepared_rows: list[dict[str, Any]] = []

    for row in rows:
        preferred_name = _primary_name(row)
        synonyms_raw = (row.get("synonyms") or "").strip()
        inchikey = (row.get("inchikey") or "").strip()
        smiles = (row.get("smiles") or "").strip()
        molecular_formula = (row.get("molecular_formula") or "").strip()
        source_db = (row.get("source_db") or "").strip()
        source_url = (row.get("source_url") or "").strip()

        canonical = preferred_name
        if not canonical:
            continue

        primary_aliases = _alias_variants(preferred_name)
        synonyms = [
            s.strip()
            for s in synonyms_raw.split("|")
            if s.strip() and any(c.isalnum() for c in s.strip())
        ]
        # Only strip parentheses and stereochemical prefixes from
        # synonyms to prevent vocabulary bloat over large datasets.
        synonym_variants = list(dict.fromkeys(s for s in synonyms if s))

        for s in list(synonyms):
            stripped = re.sub(r"[()]", "", s)
            if stripped != s and stripped not in synonym_variants:
                synonym_variants.append(stripped)
        for s in list(synonym_variants):
            stereo = re.sub(r"^\([A-Z](?:,[A-Z])*\)-", "", s)
            if stereo != s and stereo not in synonym_variants:
                synonym_variants.append(stereo)
        aliases = list(dict.fromkeys(primary_aliases + synonyms))

        prepared_rows.append(
            {
                "canonical": canonical,
                "preferred_name": preferred_name,
                "aliases": aliases,
                "match_aliases": primary_aliases,
                "synonym_aliases": synonym_variants,
                "inchikey": inchikey,
                "smiles": smiles,
                "molecular_formula": molecular_formula,
                "source_db": source_db,
                "source_url": source_url,
            }
        )

        for index, alias in enumerate(primary_aliases):
            alias_candidates.setdefault(alias.lower(), []).append(
                {
                    "canonical": canonical,
                    "alias": alias,
                    "priority": 2 if index == 0 else 1,
                }
            )
        for sv in synonym_variants:
            alias_candidates.setdefault(sv.lower(), []).append(
                {"canonical": canonical, "alias": sv, "priority": 0}
            )
    alias_owner: dict[str, str] = {}
    invalid_alias_keys: set[str] = set()

    for alias_key, candidates in alias_candidates.items():
        distinct_candidates: list[dict[str, Any]] = []
        seen_pairs = set()
        for candidate in candidates:
            pair = (candidate["canonical"], candidate["priority"])
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            distinct_candidates.append(candidate)

        max_priority = max(candidate["priority"] for candidate in distinct_candidates)
        highest_priority = [
            candidate for candidate in distinct_candidates if candidate["priority"] == max_priority
        ]

        if len(highest_priority) == 1:
            alias_owner[alias_key] = highest_priority[0]["canonical"]
            continue

        # In tie scenarios, pick the shortest canonical (most general
        # form) to resolve derivatives like oxides or acetates cleanly.
        winner = min(highest_priority, key=lambda c: len(c["canonical"]))
        alias_owner[alias_key] = winner["canonical"]

    for prepared in prepared_rows:
        canonical = prepared["canonical"]
        accepted_aliases = [
            alias
            for alias in prepared["match_aliases"]
            if alias.lower() not in invalid_alias_keys
            and alias_owner.get(alias.lower()) == canonical
        ]
        accepted_synonyms = [
            alias
            for alias in prepared["synonym_aliases"]
            if alias.lower() not in invalid_alias_keys
            and alias_owner.get(alias.lower()) == canonical
        ]
        accepted_aliases.extend(accepted_synonyms)

        if not accepted_aliases:
            continue

        aliases_by_canonical[canonical] = accepted_aliases
        metadata = {
            "canonical": canonical,
            "preferred_name": prepared["preferred_name"],
            "aliases": prepared["aliases"],
            "inchikey": prepared["inchikey"],
            "smiles": prepared["smiles"],
            "molecular_formula": prepared["molecular_formula"],
            "source_db": prepared["source_db"],
            "source_url": prepared["source_url"],
        }

        for alias in accepted_aliases:
            alias_key = alias.lower()
            terms.add(alias_key)
            canonical_map[alias_key] = canonical
            metadata_map[alias_key] = metadata
            # Also store normalized form so lookup() finds terms after
            # match() normalizes punctuation to spaces.
            norm_key = _normalize_for_match(alias_key).lower().strip()
            if norm_key != alias_key:
                metadata_map[norm_key] = metadata
                canonical_map[norm_key] = canonical

    return {
        "terms": sorted(terms),
        "canonical_map": canonical_map,
        "metadata_map": metadata_map,
        "aliases_by_canonical": aliases_by_canonical,
    }


class ChemicalMatcher:
    """High-precision chemical matcher backed by dictionary data."""

    def __init__(self, nlp: Any = None):
        """Initialize chemical matcher with an optional spaCy pipeline."""
        self.nlp = nlp or spacy.blank("en")
        self.matcher: PhraseMatcher | None = None
        self.canonical_map: dict[str, str] = {}
        self.metadata_map: dict[str, dict[str, Any]] = {}
        self.aliases_by_canonical: dict[str, list[str]] = {}
        self._load_or_build()

    def _load_or_build(self) -> None:
        """Load chemical patterns from disk cache or rebuild from CSV."""
        if CACHE_FILE.exists() and DATA_FILE.exists():
            try:
                if CACHE_FILE.stat().st_mtime < DATA_FILE.stat().st_mtime:
                    logger.info("[ChemicalMatcher] Cache is stale; rebuilding from CSV")
                    self._build_from_csv()
                    return

                with open(CACHE_FILE, "rb") as f:
                    cache = pickle.load(f)

                if ENTITY_TYPE in cache:
                    data = cache[ENTITY_TYPE]
                    if data.get("cache_version") != CACHE_VERSION:
                        logger.info("[ChemicalMatcher] Cache version mismatch; rebuilding from CSV")
                        self._build_from_csv()
                        return
                    if not data.get("metadata_map") or not data.get("canonical_map"):
                        logger.info(
                            "[ChemicalMatcher] Cache schema incomplete; rebuilding from CSV"
                        )
                        self._build_from_csv()
                        return
                    if data.get("source_mtime") != DATA_FILE.stat().st_mtime:
                        logger.info(
                            "[ChemicalMatcher] Cache source mtime mismatch; rebuilding from CSV"
                        )
                        self._build_from_csv()
                        return

                    terms = data["terms"]
                    self.canonical_map = data.get("canonical_map", {})
                    self.metadata_map = data.get("metadata_map", {})
                    self.aliases_by_canonical = data.get("aliases_by_canonical", {})

                    matcher = PhraseMatcher(self.nlp.vocab, attr="LOWER")
                    matcher.add(
                        ENTITY_TYPE,
                        [self.nlp.make_doc(_normalize_for_match(t)) for t in terms],
                    )
                    self.matcher = matcher
                    logger.info(
                        "[ChemicalMatcher] Loaded %s patterns from cache",
                        len(terms),
                    )
                    return
            except Exception as e:
                logger.warning("Chemical cache load failed: %s", e)

        self._build_from_csv()

    def _build_from_csv(self) -> None:
        """Build matcher patterns and metadata cache from chemical CSV."""
        if not DATA_FILE.exists():
            raise FileNotFoundError(f"Chemical dictionary not found: {DATA_FILE}")

        BUILD_DIR.mkdir(parents=True, exist_ok=True)

        with open(DATA_FILE, encoding="utf-8", errors="ignore", newline="") as f:
            import csv

            rows = list(csv.DictReader(f))

        cache_data = build_chemical_cache_data(
            rows,
            log_collision=lambda alias, existing, canonical: logger.debug(
                "[ChemicalMatcher] Duplicate alias collision for '%s': "
                "'%s' vs '%s'. Keeping neither.",
                alias,
                existing,
                canonical,
            ),
        )

        self.canonical_map = cache_data["canonical_map"]
        self.metadata_map = cache_data["metadata_map"]
        self.aliases_by_canonical = cache_data["aliases_by_canonical"]

        patterns = [self.nlp.make_doc(_normalize_for_match(t)) for t in cache_data["terms"]]
        matcher = PhraseMatcher(self.nlp.vocab, attr="LOWER")
        matcher.add(ENTITY_TYPE, patterns)
        self.matcher = matcher

        cache = {
            ENTITY_TYPE: {
                **cache_data,
                "cache_version": CACHE_VERSION,
                "source_mtime": DATA_FILE.stat().st_mtime,
            }
        }
        with open(CACHE_FILE, "wb") as f:
            pickle.dump(cache, f)

        logger.info("[ChemicalMatcher] Built %s chemical patterns", len(patterns))

    def lookup(self, text: str) -> dict[str, Any] | None:
        """Look up entity dictionary metadata for a chemical mention."""
        stripped = text.lower().strip()
        lookup = self.metadata_map.get(stripped)
        if not lookup:
            lookup = self.metadata_map.get(_normalize_alias(text))
        if not lookup:
            return None

        return {
            "text": text,
            "span": text,
            "canonical": lookup.get("preferred_name") or lookup.get("canonical") or text,
            "preferred_name": lookup.get("preferred_name") or lookup.get("canonical") or text,
            "aliases": lookup.get("aliases", []),
            "type": ENTITY_TYPE,
            "label": ENTITY_TYPE,
            "score": 1.0,
            "name_type": None,
            "linked_to": None,
            "inchikey": lookup.get("inchikey"),
            "smiles": lookup.get("smiles"),
            "molecular_formula": lookup.get("molecular_formula"),
            "source_db": lookup.get("source_db"),
            "source_url": lookup.get("source_url"),
        }

    def match(self, text: str) -> list[dict[str, Any]]:
        """Extract chemical entity mentions and metadata from input text."""
        if not text or not text.strip() or self.matcher is None:
            return []

        normalized_text, greek_offsets = _normalize_greek(text)
        normalized_text = re.sub(
            r"[/>,\u2032\u2019\u0027]",
            lambda m: " " * (m.end() - m.start()),
            normalized_text,
        )
        split_text, split_offsets = _split_abbreviations(normalized_text)
        offset_map = [greek_offsets[split_offsets[i]] for i in range(len(split_offsets))]

        doc = self.nlp(split_text)
        entities: list[dict[str, Any]] = []
        seen = set()

        for _, start, end in self.matcher(doc):
            span = doc[start:end]
            norm_start = span.start_char
            norm_end = span.end_char
            orig_start = offset_map[norm_start]
            orig_end = offset_map[norm_end - 1] + 1
            key = (orig_start, orig_end)
            if key in seen:
                continue
            seen.add(key)

            enriched = self.lookup(span.text)
            if not enriched:
                continue

            term = span.text
            if term.isdigit() or len(term) <= 1:
                continue
            # Filter out dangling parenthetical footnote markers (e.g.
            # '(6' or '(18') from imperfect CSV entries.
            if term[0] == "(" and len(term) <= 4 and term[1].isdigit():
                continue

            enriched.update(
                {
                    "text": text[orig_start:orig_end],
                    "span": text[orig_start:orig_end],
                    "start": orig_start,
                    "end": orig_end,
                }
            )
            entities.append(enriched)

        return entities


_matcher: ChemicalMatcher | None = None


def get_matcher() -> ChemicalMatcher:
    """Return singleton chemical matcher instance, creating if necessary."""
    global _matcher
    if _matcher is None:
        _matcher = ChemicalMatcher()
    return _matcher


def match_chemicals(text: str) -> list[dict[str, Any]]:
    """Extract chemical entities and metadata from text via singleton matcher."""
    return get_matcher().match(text)
