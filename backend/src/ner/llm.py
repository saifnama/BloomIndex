"""Large language model integration for named entity extraction.

Handles structured extraction prompts, JSON output validation, automated
correction retries, and schema normalization for phytochemical entities.
"""

import logging
import re
import time
from typing import Any

logger = logging.getLogger(__name__)

from backend.src.common.llm_client import (
    LLMAuthError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUpstreamError,
    get_llm_client,
)
from backend.src.settings import (
    NER_MAX_ATTEMPTS,
    LLMConfigError,
)


class _LLMMixin:
    """Provides LLM prompting, validation, and parsing methods for NER."""

    async def call_llm(
        self,
        text_chunk: str,
        error_hint: str | None = None,
    ) -> str:
        """Execute unified LLM query for named entity extraction.

        If error_hint is provided, prepends a targeted correction block
        to the user prompt to guide the model during retry attempts.

        Args:
            text_chunk: Document segment to analyze for entity mentions.
            error_hint: Optional validation error description from a
                preceding attempt to instruct model self-correction.

        Returns:
            Raw response text from the LLM, or an empty string on
            recoverable network, authentication, or timeout errors.

        Raises:
            LLMRateLimitError: When upstream API limits are exceeded,
                allowing caller to fail fast without futile retries.
        """
        # Prepend error feedback to prioritize correction context.
        if error_hint:
            user_content = (
                "Your previous response was rejected for the "
                f"following reason:\n  {error_hint}\n\n"
                "Retry with a corrected response that follows the "
                "schema from the system prompt.\n\n"
                f"Extract entities from:\n\n{text_chunk}"
            )
        else:
            user_content = f"Extract entities from:\n\n{text_chunk}"

        try:
            client = get_llm_client()
        except LLMConfigError as e:
            logger.error(f"NER LLM config error: {e}")
            return ""

        try:
            response = await client.invoke(
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.0,
                max_tokens=2048,
                timeout_seconds=120.0,
                json_mode=True,
            )
        except LLMRateLimitError:
            raise
        except LLMUpstreamError as e:
            # Retry in plain text if JSON mode is unsupported upstream.
            if "400" not in str(e):
                logger.warning(f"NER LLM call failed: {e}")
                return ""
            logger.warning(f"NER JSON mode unsupported ({e}); retrying plain")
            try:
                response = await client.invoke(
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_content},
                    ],
                    temperature=0.0,
                    max_tokens=2048,
                    timeout_seconds=120.0,
                )
            except LLMRateLimitError:
                raise
            except (LLMAuthError, LLMTimeoutError, LLMUpstreamError) as retry_e:
                logger.warning(f"NER LLM call failed: {retry_e}")
                return ""
        except (LLMAuthError, LLMTimeoutError) as e:
            logger.warning(f"NER LLM call failed: {e}")
            return ""
        return response.content or ""

    async def _extract_entities_with_retry(
        self,
        text_chunk: str,
        max_attempts: int | None = None,
    ) -> list[dict[str, Any]]:
        """Extract entities with iterative syntax repair and retry loops.

        Attempts LLM extraction up to max_attempts times. On each round,
        repairs syntactic defects, validates expected schema structure,
        and provides diagnostic hints on semantic validation failure.

        Args:
            text_chunk: Input document segment to extract entities from.
            max_attempts: Maximum extraction attempts before aborting.
                Defaults to NER_MAX_ATTEMPTS.

        Returns:
            List of validated entity dictionaries with text, label,
            score, and metadata. Returns empty list on exhaustion.
        """
        if not text_chunk or not text_chunk.strip():
            return []

        if max_attempts is None:
            max_attempts = max(1, NER_MAX_ATTEMPTS)

        try:
            from json_repair import repair_json
        except ImportError:
            # Fall back to single-shot parse if repair package missing.
            raw = await self.call_llm(text_chunk)
            return self.parse_llm_response(raw)

        llm_types = {"CHEMICAL", "SPECIES", "LOCATION", "BIOACTIVITY", "DISEASE"}
        error_hint: str | None = None

        for attempt in range(max_attempts):
            t0 = time.perf_counter()
            try:
                raw = await self.call_llm(text_chunk, error_hint=error_hint)
            except LLMRateLimitError as e:
                # Abort retries immediately when throttled by upstream.
                logger.warning(f"NER rate limited, skipping retries: {e}")
                return []
            llm_ms = (time.perf_counter() - t0) * 1000
            if not raw:
                error_hint = (
                    "Your previous response was empty. Return a JSON "
                    "array of entities like "
                    '[{"span":"...", "type":"CHEMICAL", "score":0.9}].'
                )
                continue

            # Remove reasoning blocks before JSON decoding.
            cleaned = re.sub(r"<reasoning>.*?</reasoning>", "", raw, flags=re.DOTALL).strip()
            if not cleaned:
                error_hint = (
                    "Your response contained only a <reasoning> block. "
                    "Return the JSON array directly, not inside reasoning tags."
                )
                continue

            try:
                parsed = repair_json(cleaned, return_objects=True)
            except Exception as e:
                logger.warning(f"NER JSON parse failed (attempt {attempt + 1}): {e}")
                error_hint = (
                    "Your previous response could not be parsed as "
                    "JSON. Return a single array, no prose around it."
                )
                continue

            # Support bare lists, top-level objects, and container keys.
            entities = None
            if isinstance(parsed, str) and parsed.strip():
                try:
                    parsed = repair_json(parsed.strip(), return_objects=True)
                except Exception:
                    pass
            if isinstance(parsed, list):
                entities = parsed
            elif isinstance(parsed, dict):
                if "span" in parsed or "text" in parsed:
                    # Wrap solitary entity object into a sequence.
                    entities = [parsed]
                else:
                    for key in ("entities", "data", "results", "items"):
                        value = parsed.get(key)
                        if isinstance(value, list):
                            entities = value
                            break
                if entities is None:
                    visible_keys = ", ".join(sorted(str(k) for k in parsed.keys())[:6]) or "<none>"
                    error_hint = (
                        "Expected a top-level JSON array, e.g. "
                        '[{"span":"...", "type":"CHEMICAL"}]. Your '
                        f"object had keys: {visible_keys}."
                    )
                    continue
            else:
                error_hint = (
                    f"Expected a JSON array of entities; got a {type(parsed).__name__} instead."
                )
                continue

            # Validate attributes and normalize known category aliases.
            remap = {"DRUG": "CHEMICAL"}
            result: list[dict[str, Any]] = []
            for e in entities:
                if not isinstance(e, dict):
                    continue
                text = str(e.get("span", e.get("text", ""))).strip()
                label = str(e.get("type", e.get("label", ""))).strip().upper()
                label = remap.get(label, label)
                if label not in llm_types:
                    continue
                if not text or label not in self.all_labels:
                    continue
                score = float(e.get("score", 1.0))
                result.append(
                    {
                        "text": text,
                        "label": label,
                        "score": score,
                        "start": e.get("start"),
                        "end": e.get("end"),
                        "name_type": e.get("name_type"),
                        "linked_to": e.get("linked_to"),
                    }
                )

            if not result:
                # Guide model when no candidate entity matched schema.
                error_hint = (
                    "None of the entities you returned passed validation. "
                    "Use exact uppercase types from this set: "
                    f"{sorted(llm_types)}. Each item needs "
                    '"span" (entity text) and "type" (one of the labels).'
                )
                continue

            if attempt > 0:
                logger.info(
                    f"NER extraction succeeded on attempt {attempt + 1}/"
                    f"{max_attempts} after validation-retry "
                    f"({len(result)} entities, {llm_ms:.0f}ms LLM)"
                )
            else:
                logger.info(f"NER extraction: {len(result)} entities in {llm_ms:.0f}ms")
            return result

        logger.warning(
            f"NER extraction: all {max_attempts} attempts failed; final error hint: {error_hint!r}"
        )
        return []

    def parse_llm_response(self, raw_text: str) -> list[dict[str, Any]]:
        """Parse raw model output into normalized entity records.

        Strips non-JSON wrappers, recovers malformed syntax via
        json_repair, remaps labels, and enforces domain filters.

        Args:
            raw_text: Unprocessed text response returned by the LLM.

        Returns:
            List of parsed entity dictionaries matching internal schema.
        """
        # Remove chain-of-thought blocks before JSON parsing.
        raw_text = re.sub(r"<reasoning>.*?</reasoning>", "", raw_text, flags=re.DOTALL).strip()
        if not raw_text:
            return []

        try:
            from json_repair import repair_json

            parsed = repair_json(raw_text, return_objects=True)
        except Exception:
            return []

        # Extract entities from list sequences or wrapped dict payloads.
        if isinstance(parsed, list):
            entities = parsed
        elif isinstance(parsed, dict):
            if "span" in parsed or "text" in parsed:
                entities = [parsed]
            else:
                entities = None
                for key in ("entities", "data", "results", "items"):
                    value = parsed.get(key)
                    if isinstance(value, list):
                        entities = value
                        break
                if entities is None:
                    return []
        else:
            return []

        try:
            result = []
            for e in entities:
                if not isinstance(e, dict):
                    continue
                # Support both span/type and text/label schemas.
                text = str(e.get("span", e.get("text", ""))).strip()
                label = str(e.get("type", e.get("label", ""))).strip().upper()
                # Remap LLM labels to canonical domain categories.
                remap = {
                    "DRUG": "CHEMICAL",
                }
                label = remap.get(label, label)

                # Filter against supported LLM target categories.
                llm_types = {"CHEMICAL", "SPECIES", "LOCATION", "BIOACTIVITY", "DISEASE"}
                if label not in llm_types:
                    continue

                score = float(e.get("score", 1.0))
                if text and label in self.all_labels:
                    result.append(
                        {
                            "text": text,
                            "label": label,
                            "score": score,
                            "name_type": e.get("name_type"),
                            "linked_to": e.get("linked_to"),
                        }
                    )
            return result
        except Exception:
            return []


SYSTEM_PROMPT = """You are a precise Named Entity Recognition (NER) specialist for phytochemical and ethnobotanical research.

Extract named entities from scientific text and return ONLY a JSON array — no prose, no markdown fences.

ENTITY TYPES (exactly five — extract nothing else)

1. CHEMICAL — named compounds, phytoconstituents, solvents, reagents. NOT bulk mixtures (essential oil, crude extract).
   Examples: eugenol, quercetin, methanol, streptozotocin

2. SPECIES — plant binomial name only (genus + species). NO common names, NO non-plant organisms.
   Examples: Ocimum sanctum, Cinnamomum verum, Azadirachta indica

3. LOCATION — geographic region, country, state, district, forest.
   Examples: Western Ghats, Wayanad, Kerala, Tamil Nadu

4. BIOACTIVITY — biological or pharmacological activity.
   Examples: antimicrobial, antioxidant, anti-inflammatory, cytotoxic

5. DISEASE — named clinical/veterinary condition (NOT mechanisms like apoptosis or oxidative stress).
   Examples: malaria, diabetes, tuberculosis, cancer

RULES
- Solvents/pharmacological inducers are always CHEMICAL.
- Extract nested entities separately: "streptozotocin-induced diabetes" → CHEMICAL + DISEASE.
- Never include concentration values in spans: "Eugenol (72.4%)" → span is "Eugenol".
- linked_to is only for BIOACTIVITY → name of the performing CHEMICAL, or null.

OUTPUT SCHEMA — JSON array, each object:
{"span":"verbatim substring","type":"CHEMICAL|SPECIES|LOCATION|BIOACTIVITY|DISEASE","start":0,"end":7,"name_type":"scientific|null","linked_to":"chemical span|null"}

name_type is "scientific" for SPECIES, null for all others.
linked_to is only populated for BIOACTIVITY when the performing chemical is named in the text.

Return ONLY the JSON array."""


LABEL_DEFINITIONS = {
    # Categories resolved deterministically via dictionary matchers.
    "PLANT PART": "Plant morphological structures (leaf, bark, root, flower, etc.).",
    "ANALYTICAL TECHNIQUE": "Specific separation or analytical technique.",
    "EXTRACTION METHOD": "Physical or mechanical extraction process.",
    "DEVELOPMENT STAGE": "Plant development stage (seedling, flowering, etc.).",
    "SEASON": "Seasonal reference (monsoon, winter, etc.).",
    # Categories extracted via contextual LLM prompting.
    "CHEMICAL": "Chemical compounds, natural molecules, phytochemicals.",
    "SPECIES": "Living organisms (plants, bacteria, fungi, animals).",
    "LOCATION": "Geographic locations, regions, institutions.",
    "BIOACTIVITY": "Biological or chemical activity of substances.",
    "DISEASE": "Medical conditions, diseases, disorders.",
}
