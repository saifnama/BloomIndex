"""Dictionary-based entity recognition and dictionary compilation.

Aggregates curated phrase matchers across botanical, chemical, and
experimental domain categories and provides span deduplication.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

from backend.src.ner.dictionaries.analytical_technique import match_analytical_techniques
from backend.src.ner.dictionaries.bioactivity import match_bioactivities
from backend.src.ner.dictionaries.chemical import match_chemicals
from backend.src.ner.dictionaries.plant_part import match_plant_parts
from backend.src.ner.dictionaries.species import match_species
from backend.src.settings import (
    NER_CHUNK_WORDS,
)


class _DictionaryMixin:
    """Provides dictionary matching and chunking methods for NER."""

    def _match_dictionary_in_text(self, text: str) -> list[dict[str, Any]]:
        """Run all dictionary matchers on text and return normalized entities.

        Scans input text across botanical parts, analytical techniques,
        extraction methods, development stages, seasons, species,
        chemicals, and bioactivities. Deduplicates overlapping spans by
        prioritizing longer matches.

        Args:
            text: Input document text to scan for entity mentions.

        Returns:
            List of normalized entity dictionaries sorted by document
            span offsets.
        """
        entities = []

        # Plant parts.
        for e in match_plant_parts(text):
            entities.append(
                {
                    "text": e.get("span", e.get("text", "")),
                    "label": e.get("type", e.get("label", "PLANT PART")),
                    "score": e.get("score", 1.0),
                    "start": e.get("start"),
                    "end": e.get("end"),
                    "canonical": e.get("canonical"),
                    "aliases": e.get("aliases"),
                }
            )

        # Analytical techniques.
        for e in match_analytical_techniques(text):
            entities.append(
                {
                    "text": e.get("span", e.get("text", "")),
                    "label": e.get("type", e.get("label", "ANALYTICAL TECHNIQUE")),
                    "score": e.get("score", 1.0),
                    "start": e.get("start"),
                    "end": e.get("end"),
                    "canonical": e.get("canonical"),
                    "aliases": e.get("aliases"),
                }
            )

        # Extraction methods.
        from backend.src.ner.dictionaries.extraction_method import match_extraction_methods

        for e in match_extraction_methods(text):
            entities.append(
                {
                    "text": e.get("span", e.get("text", "")),
                    "label": e.get("type", e.get("label", "EXTRACTION METHOD")),
                    "score": e.get("score", 1.0),
                    "start": e.get("start"),
                    "end": e.get("end"),
                    "canonical": e.get("canonical"),
                    "aliases": e.get("aliases"),
                }
            )

        # Development stages.
        from backend.src.ner.dictionaries.development_stage import match_development_stages

        for e in match_development_stages(text):
            entities.append(
                {
                    "text": e.get("span", e.get("text", "")),
                    "label": e.get("type", e.get("label", "DEVELOPMENT STAGE")),
                    "score": e.get("score", 1.0),
                    "start": e.get("start"),
                    "end": e.get("end"),
                    "canonical": e.get("canonical"),
                    "aliases": e.get("aliases"),
                }
            )

        # Seasons.
        from backend.src.ner.dictionaries.season import match_seasons

        for e in match_seasons(text):
            entities.append(
                {
                    "text": e.get("span", e.get("text", "")),
                    "label": e.get("type", e.get("label", "SEASON")),
                    "score": e.get("score", 1.0),
                    "start": e.get("start"),
                    "end": e.get("end"),
                    "canonical": e.get("canonical"),
                    "aliases": e.get("aliases"),
                }
            )

        # Species.
        for e in match_species(text):
            entities.append(
                {
                    "text": e.get("span", e.get("text", "")),
                    "label": e.get("type", e.get("label", "SPECIES")),
                    "score": e.get("score", 1.0),
                    "start": e.get("start"),
                    "end": e.get("end"),
                    "canonical": e.get("canonical"),
                    "aliases": e.get("aliases"),
                    "name_type": e.get("name_type"),
                    "accepted_scientific_name": e.get("accepted_scientific_name"),
                    "common_name": e.get("common_name"),
                    "source_db": e.get("source_db"),
                    "source_url": e.get("source_url"),
                    "taxon_id": e.get("taxon_id"),
                    "match_status": e.get("match_status"),
                    "review_required": e.get("review_required"),
                    "scientific_name_verified": e.get("scientific_name_verified"),
                }
            )

        # Chemicals.
        for e in match_chemicals(text):
            entities.append(
                {
                    "text": e.get("span", e.get("text", "")),
                    "label": e.get("type", e.get("label", "CHEMICAL")),
                    "score": e.get("score", 1.0),
                    "start": e.get("start"),
                    "end": e.get("end"),
                    "canonical": e.get("canonical"),
                    "preferred_name": e.get("preferred_name"),
                    "aliases": e.get("aliases"),
                    "inchikey": e.get("inchikey"),
                    "smiles": e.get("smiles"),
                    "molecular_formula": e.get("molecular_formula"),
                    "source_db": e.get("source_db"),
                    "source_url": e.get("source_url"),
                }
            )

        # Bioactivities.
        for e in match_bioactivities(text):
            entities.append(
                {
                    "text": e.get("span", e.get("text", "")),
                    "label": e.get("type", e.get("label", "BIOACTIVITY")),
                    "score": e.get("score", 1.0),
                    "start": e.get("start"),
                    "end": e.get("end"),
                    "canonical": e.get("canonical"),
                    "synonyms": e.get("synonyms"),
                }
            )

        # Resolve overlapping spans by prioritizing the longest match to
        # prevent partial fragments from masking complete entities.
        kept = []
        for e in sorted(entities, key=lambda x: (x.get("start") or 0, -(x.get("end") or 0))):
            s, en = e.get("start"), e.get("end")
            if s is None or en is None:
                kept.append(e)
                continue
            overlaps = any(
                s < k["end"] and en > k["start"] for k in kept if "start" in k and "end" in k
            )
            if not overlaps:
                kept.append(e)
        entities = kept

        return entities

    def split_into_word_chunks(self, text: str, chunk_size: int = NER_CHUNK_WORDS) -> list[str]:
        """Partition text into word-bounded segments of fixed maximum size.

        Args:
            text: Input document text to segment.
            chunk_size: Maximum word count per chunk. Defaults to
                NER_CHUNK_WORDS.

        Returns:
            List of text chunk strings preserving word boundaries.
        """
        words = text.split()
        chunks = []
        for i in range(0, len(words), chunk_size):
            chunk = " ".join(words[i : i + chunk_size])
            if chunk.strip():
                chunks.append(chunk)
        return chunks


def enrich_chemical_like_entity(entity: dict[str, Any], chemical_matcher: Any) -> None:
    """Populate chemical structure and database identifiers on an entity.

    Performs dictionary lookup for chemical metadata (InChIKey, SMILES,
    formula, preferred name) and updates the entity dict in place.

    Args:
        entity: Entity dictionary to enrich with chemical metadata.
        chemical_matcher: ChemicalMatcher instance providing dictionary
            lookup and canonical mappings.
    """
    chemical_metadata = chemical_matcher.lookup(entity.get("text", ""))
    if chemical_metadata:
        for key, value in chemical_metadata.items():
            if key in {"text", "span", "start", "end"}:
                continue
            if value not in (None, "", []):
                entity[key] = value
    if not entity.get("canonical"):
        text_lower = entity.get("text", "").lower()
        entity["canonical"] = chemical_matcher.canonical_map.get(text_lower, entity.get("text", ""))


def preload_dictionaries() -> int:
    """Initialize all domain dictionary matchers in memory.

    Instantiates all singleton phrase matchers at startup to prevent
    compilation latency spikes on the first user request.

    Returns:
        Total number of dictionary matchers preloaded.
    """
    from backend.src.ner.dictionaries.analytical_technique import (
        get_matcher as get_analytical_matcher,
    )
    from backend.src.ner.dictionaries.bioactivity import (
        get_matcher as get_bioactivity_matcher,
    )
    from backend.src.ner.dictionaries.chemical import (
        get_matcher as get_chemical_matcher,
    )
    from backend.src.ner.dictionaries.development_stage import (
        get_matcher as get_development_matcher,
    )
    from backend.src.ner.dictionaries.extraction_method import (
        get_matcher as get_extraction_matcher,
    )
    from backend.src.ner.dictionaries.plant_part import (
        get_matcher as get_plant_matcher,
    )
    from backend.src.ner.dictionaries.season import (
        get_matcher as get_season_matcher,
    )
    from backend.src.ner.dictionaries.species import (
        get_matcher as get_species_matcher,
    )

    loaders = (
        get_analytical_matcher,
        get_bioactivity_matcher,
        get_chemical_matcher,
        get_development_matcher,
        get_extraction_matcher,
        get_plant_matcher,
        get_season_matcher,
        get_species_matcher,
    )
    for load in loaders:
        load()
    return len(loaders)
