"""Named Entity Recognition (NER) pipeline service.

Coordinates dictionary pattern matchers, contextual LLM extractions,
canonical entity normalization, and confidence-based deduplication.
"""

import asyncio
import logging
import re
import time
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)

from backend.src.ner.dictionary import _DictionaryMixin, enrich_chemical_like_entity
from backend.src.ner.llm import LABEL_DEFINITIONS, _LLMMixin
from backend.src.settings import (
    NER_BUDGET_SECONDS,
    NER_CONFIDENCE_THRESHOLD,
    NER_HYBRID,
    _safe_int,
)


class NERService(_DictionaryMixin, _LLMMixin):
    """Hybrid named entity recognition service.

    Combines deterministic dictionary matchers with contextual LLM
    extraction, entity normalization, and confidence deduplication.
    """

    def __init__(self):
        self.all_labels = list(LABEL_DEFINITIONS.keys())
        self.result_cache = {}  # DOI lookup cache for extracted entities.

    async def process_text(self, text: str, max_chunks: int = 3) -> list[dict[str, Any]]:
        """Extract and normalize entities from unsegmented text.

        Args:
            text: Input text to process.
            max_chunks: Maximum number of chunks to process.

        Returns:
            Tuple of entity summary mapping and filtered entity list.
        """
        # Deterministic dictionary matchers resolve unambiguous spans.
        dict_entities = self._match_dictionary_in_text(text)

        # Truncate input chunks to respect token and latency budgets.
        chunks = self.split_into_word_chunks(text)
        if len(chunks) > max_chunks:
            chunks = chunks[:max_chunks]
            text = " ".join(chunks)
        else:
            text = text

        # Contextual extraction via LLM with validation retries.
        llm_entities = []
        if not NER_HYBRID:
            logger.info("NER_HYBRID=false — dictionary-only extraction.")
        else:
            try:
                for chunk in chunks:
                    parsed = await self._extract_entities_with_retry(chunk)
                    llm_entities.extend(parsed)
            except Exception as e:
                logger.warning(f"LLM extraction failed: {e}. Using dictionary entities only.")

        # Combine dictionary matches and LLM extractions.
        all_entities = dict_entities + llm_entities

        # Normalize extracted mentions to canonical entities.
        from backend.src.ner.dictionaries.analytical_technique import (
            get_matcher as get_analytical_matcher,
        )
        from backend.src.ner.dictionaries.plant_part import (
            get_matcher as get_plant_matcher,
        )

        plant_matcher = get_plant_matcher()
        analytical_matcher = get_analytical_matcher()
        from backend.src.ner.dictionaries.extraction_method import (
            get_matcher as get_extraction_matcher,
        )

        extraction_matcher = get_extraction_matcher()
        from backend.src.ner.dictionaries.development_stage import (
            get_matcher as get_development_matcher,
        )

        development_matcher = get_development_matcher()
        from backend.src.ner.dictionaries.season import (
            get_matcher as get_season_matcher,
        )

        season_matcher = get_season_matcher()
        from backend.src.ner.dictionaries.chemical import (
            get_matcher as get_chemical_matcher,
        )
        from backend.src.ner.dictionaries.species import get_matcher as get_species_matcher

        species_matcher = get_species_matcher()
        chemical_matcher = get_chemical_matcher()

        for e in all_entities:
            text_lower = e.get("text", "").lower()
            label = e.get("label", "")
            if label == "SPECIES":
                species_metadata = species_matcher.lookup(e.get("text", ""))
                if species_metadata:
                    for key, value in species_metadata.items():
                        if key in {"text", "span", "start", "end"}:
                            continue
                        if value not in (None, "", []):
                            e[key] = value
                if not e.get("canonical"):
                    e["canonical"] = species_matcher.canonical_map.get(
                        text_lower, e.get("text", "")
                    )
            elif label == "CHEMICAL":
                enrich_chemical_like_entity(e, chemical_matcher)
            elif e.get("canonical"):
                continue
            elif label == "PLANT PART":
                e["canonical"] = plant_matcher.canonical_map.get(text_lower, e.get("text", ""))
            elif label == "ANALYTICAL TECHNIQUE":
                e["canonical"] = analytical_matcher.canonical_map.get(text_lower, e.get("text", ""))
            elif label == "EXTRACTION METHOD":
                e["canonical"] = extraction_matcher.canonical_map.get(text_lower, e.get("text", ""))
            elif label == "DEVELOPMENT STAGE":
                e["canonical"] = development_matcher.canonical_map.get(
                    text_lower, e.get("text", "")
                )
            elif label == "SEASON":
                e["canonical"] = season_matcher.canonical_map.get(text_lower, e.get("text", ""))

        summary, filtered = self.deduplicate(all_entities, text)
        return summary, filtered

    async def process_sections(self, sections: list[dict[str, str]]) -> tuple:
        """Extract and normalize entities across document sections.

        Args:
            sections: List of dicts containing section title and text.

        Returns:
            Tuple of entity summary mapping and filtered entity list.
        """
        if not sections:
            return {}, []

        # Filter empty sections to avoid redundant async tasks.
        valid_sections = [s for s in sections if (s.get("content", "") or "").strip()]
        if not valid_sections:
            return {}, []

        # Synchronous dictionary matching runs sequentially per section.
        all_dict_entities: list[dict[str, Any]] = []
        for section in valid_sections:
            section_title = section.get("title", "Unknown")
            section_text = section.get("content", "")
            for ent in self._match_dictionary_in_text(section_text):
                ent["section"] = section_title
                all_dict_entities.append(ent)

        # Bounded concurrency avoids overloading local inference.
        all_llm_entities: list[dict[str, Any]] = []
        if not NER_HYBRID:
            logger.info("NER_HYBRID=false — dictionary-only extraction.")
            skipped_sections = 0
        else:
            _llm_concurrency = max(1, _safe_int("NER_CONCURRENCY", 1))
            sem = asyncio.Semaphore(_llm_concurrency)

            # Hard budget prevents slow providers from stalling.
            budget_seconds = NER_BUDGET_SECONDS
            deadline = time.perf_counter() + budget_seconds if budget_seconds > 0 else None
            skipped_sections = 0

            async def _llm_for_section(section: dict[str, str]) -> list[dict[str, Any]]:
                nonlocal skipped_sections
                section_title = section.get("title", "Unknown")
                section_text = section.get("content", "")
                async with sem:
                    # Re-check deadline after acquiring semaphore lock.
                    if deadline is not None and time.perf_counter() > deadline:
                        skipped_sections += 1
                        return []
                    try:
                        parsed = await self._extract_entities_with_retry(section_text)
                    except Exception as exc:
                        logger.warning(
                            f"LLM extraction failed for section '{section_title}': {exc}"
                        )
                        return []
                return [{**e, "section": section_title} for e in parsed]

            # Prevent single section failures aborting the batch.
            section_results = await asyncio.gather(
                *[_llm_for_section(s) for s in valid_sections],
                return_exceptions=True,
            )

            for result in section_results:
                if isinstance(result, BaseException):
                    logger.warning(f"LLM section task raised: {result}")
                    continue
                all_llm_entities.extend(result)

            if skipped_sections:
                logger.warning(
                    f"NER LLM budget ({NER_BUDGET_SECONDS:.0f}s) exhausted: "
                    f"{skipped_sections}/{len(valid_sections)} sections fell back "
                    f"to dictionary-only entities"
                )

        normalized = self._normalize_entities(all_dict_entities + all_llm_entities)

        # Validate extracted spans against full concatenated text.
        full_text = " ".join([s.get("content", "") for s in sections])

        summary, filtered = self.deduplicate(normalized, full_text)
        return summary, filtered

    def _normalize_entities(self, all_entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Map entity spans and labels to standardized vocabulary forms."""
        from backend.src.ner.dictionaries.analytical_technique import (
            get_matcher as get_analytical_matcher,
        )
        from backend.src.ner.dictionaries.chemical import get_matcher as get_chemical_matcher
        from backend.src.ner.dictionaries.development_stage import (
            get_matcher as get_development_matcher,
        )
        from backend.src.ner.dictionaries.extraction_method import (
            get_matcher as get_extraction_matcher,
        )
        from backend.src.ner.dictionaries.plant_part import get_matcher as get_plant_matcher
        from backend.src.ner.dictionaries.season import get_matcher as get_season_matcher
        from backend.src.ner.dictionaries.species import get_matcher as get_species_matcher

        plant_matcher = get_plant_matcher()
        analytical_matcher = get_analytical_matcher()
        extraction_matcher = get_extraction_matcher()
        development_matcher = get_development_matcher()
        season_matcher = get_season_matcher()
        species_matcher = get_species_matcher()
        chemical_matcher = get_chemical_matcher()

        for e in all_entities:
            text_lower = e.get("text", "").lower()
            label = e.get("label", "")

            if label == "SPECIES":
                species_metadata = species_matcher.lookup(e.get("text", ""))
                if species_metadata:
                    for key, value in species_metadata.items():
                        if key in {"text", "span", "start", "end"}:
                            continue
                        if value not in (None, "", []):
                            e[key] = value
                if not e.get("canonical"):
                    e["canonical"] = species_matcher.canonical_map.get(
                        text_lower, e.get("text", "")
                    )
            elif label == "CHEMICAL":
                chemical_metadata = chemical_matcher.lookup(e.get("text", ""))
                if chemical_metadata:
                    for key, value in chemical_metadata.items():
                        if key in {"text", "span", "start", "end"}:
                            continue
                        if value not in (None, "", []):
                            e[key] = value
                if not e.get("canonical"):
                    e["canonical"] = chemical_matcher.canonical_map.get(
                        text_lower, e.get("text", "")
                    )
            elif label == "PLANT PART":
                e["canonical"] = plant_matcher.canonical_map.get(text_lower, e.get("text", ""))
            elif label == "ANALYTICAL TECHNIQUE":
                e["canonical"] = analytical_matcher.canonical_map.get(text_lower, e.get("text", ""))
            elif label == "EXTRACTION METHOD":
                e["canonical"] = extraction_matcher.canonical_map.get(text_lower, e.get("text", ""))
            elif label == "DEVELOPMENT STAGE":
                e["canonical"] = development_matcher.canonical_map.get(
                    text_lower, e.get("text", "")
                )
            elif label == "SEASON":
                e["canonical"] = season_matcher.canonical_map.get(text_lower, e.get("text", ""))

        return all_entities

    def deduplicate(
        self,
        all_entities: list[dict[str, Any]],
        full_text: str,
        threshold: float = NER_CONFIDENCE_THRESHOLD,
    ):
        """Filter low-confidence extractions and aggregate occurrences.

        Args:
            all_entities: Raw extracted entities from matchers and LLM.
            full_text: Complete document text used to count occurrences.
            threshold: Minimum entity confidence score required.

        Returns:
            Tuple of entity summary mapping and filtered entity list.
        """
        filtered = [e for e in all_entities if e["score"] >= threshold]

        # Aggregate scores and track casing variants per canonical key.
        id_map = defaultdict(lambda: {"scores": [], "variants": defaultdict(int)})

        for e in filtered:
            text = e["text"].strip()
            label = e["label"]
            lower_text = text.lower()
            key = (lower_text, label)
            id_map[key]["scores"].append(e["score"])
            id_map[key]["variants"][text] += 1

        summary = defaultdict(list)
        full_text_lower = full_text.lower()

        # Resolve dominant surface casing and count frequencies.
        for (lower_text, label), data in id_map.items():
            avg_score = sum(data["scores"]) / len(data["scores"])

            # Select most frequent surface casing across extractions.
            display_text = max(data["variants"].items(), key=lambda x: x[1])[0]

            # Count exact word-boundary matches in full text.
            try:
                escaped_text = re.escape(lower_text)
                pattern = r"(?<!\w)" + escaped_text + r"(?!\w)"
                true_count = len(re.findall(pattern, full_text_lower))
            except:
                true_count = full_text_lower.count(lower_text)

            # Exclude mentions not present in source text.
            if true_count > 0:
                summary[label].append(
                    {
                        "text": display_text,
                        "count": true_count,
                        "avg_score": round(avg_score, 2),
                    }
                )

        for label in summary:
            summary[label].sort(key=lambda x: x["count"], reverse=True)

        return dict(summary), filtered


ner_service = NERService()
