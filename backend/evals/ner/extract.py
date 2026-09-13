"""Generate predictions for dictionary, LLM, and hybrid configurations.

Outputs:
    data/dictionary.json       -- dictionary-only spans
    data/llm.json              -- LLM-only spans
    data/hybrid.json           -- merged spans (union + deduplication)

Usage:
    python extract.py
    python extract.py --skip-llm   # dict + hybrid only
    python extract.py --llm-only   # re-run LLM only

Executes each pipeline configuration against evaluation texts and
persists standardized span extractions for downstream metric
computation.
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Ensure the repository root is importable when executed directly.
_project_root = Path(__file__).resolve().parents[3]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from backend.src.ner.service import ner_service

_SCRIPT_DIR = Path(__file__).resolve().parent
_DATA_DIR = _SCRIPT_DIR / "data"
GOLD_PATH = str(_DATA_DIR / "annotated_data.json")
CHECKPOINT = str(_DATA_DIR / "config2_llm_checkpoint.json")


def load_texts(gold_path: str, max_docs: int = 0) -> dict:
    """Extract {doc_id: text} from annotated data."""
    with open(gold_path, encoding="utf-8") as f:
        tasks = json.load(f)
    texts = {}
    for task in tasks:
        if max_docs and len(texts) >= max_docs:
            break
        anns = task.get("annotations", [])
        if not anns:
            continue
        doc_id = task["data"].get("doc_id") or task["data"].get("doi", str(task["id"]))
        texts[doc_id] = task["data"]["text"]
    print(f"  Loaded {len(texts)} documents (max_docs={max_docs or 'all'})")
    return texts


def run_dictionary(text: str) -> list:
    entities = ner_service._match_dictionary_in_text(text)
    return [
        {"text": e["text"], "label": e["label"], "start": e["start"], "end": e["end"]}
        for e in entities
    ]


def run_config1(texts: dict) -> dict:
    print("Config 1 -- Dictionary (SpaCy PhraseMatcher)...")
    config1 = {}
    for doc_id, text in texts.items():
        config1[doc_id] = run_dictionary(text)
    total = sum(len(v) for v in config1.values())
    print(f"  {len(config1)} docs processed -- {total} total entities")
    return config1


async def _llm_for_doc(text: str) -> list:
    """Extract entities via LLM and locate all textual occurrences.

    LLM often return extracted phrases once rather than
    every occurrence in the passage. Locating all substring positions
    mirrors production extraction deduplication and aligns with
    token-level evaluation spans.
    """
    try:
        entities = await ner_service._extract_entities_with_retry(text)
    except Exception as e:
        print(f" LLM extraction error: {e}")
        return []

    seen_pairs = set()
    unique = []
    for ent in entities:
        label = ent.get("label", "")
        span = ent.get("text", "").strip()
        if not span or not label:
            continue
        key = (span, label)
        if key not in seen_pairs:
            seen_pairs.add(key)
            unique.append((span, label))

    valid = []
    for span, label in unique:
        pos = 0
        while True:
            pos = text.find(span, pos)
            if pos == -1:
                break
            valid.append(
                {
                    "text": span,
                    "label": label,
                    "start": pos,
                    "end": pos + len(span),
                }
            )
            pos += 1

    # The evaluator resolves overlapping spans greedily; ordering by
    # start ascending and length descending ensures exact matches take
    # precedence over shorter overlapping candidates.
    valid.sort(key=lambda s: (s["start"], -(s["end"] - s["start"])))
    return valid


def run_config2(texts: dict) -> dict:
    """Execute LLM extraction across texts with periodic checkpointing.

    Saves cumulative results every ten documents to allow resumption
    without re-querying previously processed abstracts if a run fails.
    """
    config2 = {}
    if os.path.exists(CHECKPOINT):
        with open(CHECKPOINT) as f:
            config2 = json.load(f)
        print(f"  Resuming from checkpoint -- {len(config2)}/{len(texts)} already done")

    doc_ids = list(texts.keys())
    remaining = [d for d in doc_ids if d not in config2]

    config_name = "LLM (production NER engine)"
    print(f"Config 2 -- {config_name} -- {len(remaining)} abstracts to process...")

    async def _run_all():
        for i, doc_id in enumerate(remaining):
            n = doc_ids.index(doc_id) + 1
            print(f"  [{n:3d}/{len(doc_ids)}] {doc_id[-20:]}...", end=" ", flush=True)
            entities = await _llm_for_doc(texts[doc_id])
            config2[doc_id] = entities
            print(f"-> {len(entities)} entities")

            if (i + 1) % 10 == 0:
                with open(CHECKPOINT, "w") as f:
                    json.dump(config2, f)

            # Throttle requests to stay within proxy and rate limits.
            if i < len(remaining) - 1:
                await asyncio.sleep(1)

        with open(CHECKPOINT, "w") as f:
            json.dump(config2, f)
        return config2

    result = asyncio.run(_run_all())
    total = sum(len(v) for v in result.values())
    print(f"  Done -- {total} total entities")
    return result


def merge_spans(dict_spans: list, llm_spans: list) -> list:
    """Combine dictionary and LLM extractions with position deduplication.

    Identical spans sharing both position and label are deduplicated,
    while distinct labels at matching positions are retained because
    the metric evaluator assesses each entity class independently.
    """
    merged = list(dict_spans)
    seen = {(s["start"], s["end"], s["label"]) for s in dict_spans}
    for span in llm_spans:
        key = (span["start"], span["end"], span["label"])
        if key not in seen:
            merged.append(span)
            seen.add(key)
    # Ensure exact boundary matches precede shorter partial overlaps
    # for greedy evaluation.
    merged.sort(key=lambda s: (s["start"], -(s["end"] - s["start"])))
    return merged


def run_config3(texts: dict, config1: dict, config2: dict) -> dict:
    print("Config 3 -- Hybrid (Dictionary + LLM union)...")
    config3 = {
        doc_id: merge_spans(config1.get(doc_id, []), config2.get(doc_id, [])) for doc_id in texts
    }
    total = sum(len(v) for v in config3.values())
    print(f"  {len(config3)} docs processed -- {total} total entities")
    return config3


def save(data: dict, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  Saved {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skip-llm", action="store_true", help="Run dict and hybrid only (skip LLM)"
    )
    parser.add_argument(
        "--llm-only", action="store_true", help="Re-run LLM only and rebuild hybrid"
    )
    parser.add_argument(
        "--max-docs", type=int, default=0, help="Limit to N documents (default: all)"
    )
    args = parser.parse_args()

    print("=" * 55)
    print("  Extract -- Run System Configurations")
    print("=" * 55)

    texts = load_texts(GOLD_PATH, max_docs=args.max_docs)
    print(f"Loaded {len(texts)} documents from {GOLD_PATH}\n")

    if not args.llm_only:
        config1 = run_config1(texts)
        save(config1, str(_DATA_DIR / "dictionary.json"))
    else:
        with open(_DATA_DIR / "dictionary.json") as f:
            config1 = json.load(f)
        print(f"Config 1 -- loaded from {_DATA_DIR / 'dictionary.json'} (--llm-only)")

    if args.skip_llm:
        p = _DATA_DIR / "llm.json"
        if p.exists():
            with open(p) as f:
                config2 = json.load(f)
            print(f"Config 2 -- loaded from {p} (--skip-llm)")
        else:
            print("Config 2 -- skipped (no existing file found)")
            config2 = {doc_id: [] for doc_id in texts}
    else:
        config2 = run_config2(texts)
        save(config2, str(_DATA_DIR / "llm.json"))

    config3 = run_config3(texts, config1, config2)
    save(config3, str(_DATA_DIR / "hybrid.json"))

    print()
    print("=" * 55)
    print("  All configs saved. Run evaluate.py next.")
    print("=" * 55)
