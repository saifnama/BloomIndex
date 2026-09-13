"""Evaluate NER system configurations against annotated gold standard data.

Computes SemEval-2013 standard precision, recall, and F1 metrics across
dictionary, LLM, and hybrid extraction outputs using nervaluate.
"""

import csv
import json
import os
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_DATA_DIR = _SCRIPT_DIR / "data"
_RESULTS_DIR = _SCRIPT_DIR / "results"

GOLD_PATH = str(_DATA_DIR / "annotated_data.json")

CONFIGS = {
    "Dictionary": str(_DATA_DIR / "dictionary.json"),
    "LLM": str(_DATA_DIR / "llm.json"),
    "Hybrid": str(_DATA_DIR / "hybrid.json"),
}

ALL_TYPES = [
    "CHEMICAL",
    "SPECIES",
    "BIOACTIVITY",
    "DISEASE",
    "LOCATION",
    "PLANT PART",
    "EXTRACTION METHOD",
    "ANALYTICAL TECHNIQUE",
    "DEVELOPMENT STAGE",
    "SEASON",
]

CONFIG_TYPES = {
    "Dictionary": [
        "CHEMICAL",
        "SPECIES",
        "BIOACTIVITY",
        "ANALYTICAL TECHNIQUE",
        "EXTRACTION METHOD",
        "PLANT PART",
        "DEVELOPMENT STAGE",
        "SEASON",
    ],
    "LLM": [
        "CHEMICAL",
        "SPECIES",
        "BIOACTIVITY",
        "LOCATION",
        "DISEASE",
    ],
    "Hybrid": [
        "CHEMICAL",
        "SPECIES",
        "BIOACTIVITY",
        "LOCATION",
        "DISEASE",
        "ANALYTICAL TECHNIQUE",
        "EXTRACTION METHOD",
        "PLANT PART",
        "DEVELOPMENT STAGE",
        "SEASON",
    ],
}


def load_gold(path: str):
    """Load evaluation ground truth from annotated data export.

    Offsets in the annotated data export are character-based, which
    aligns with nervaluate requirements without index conversions.
    """
    with open(path, encoding="utf-8") as f:
        tasks = json.load(f)

    gold, texts = {}, {}
    for task in tasks:
        anns = task.get("annotations", [])
        if not anns:
            continue
        doc_id = task["data"].get("doc_id") or task["data"].get("doi", str(task["id"]))
        texts[doc_id] = task["data"]["text"]
        spans = []
        for result in anns[0].get("result", []):
            v = result.get("value", {})
            if "labels" in v and v["labels"]:
                spans.append(
                    {
                        "label": v["labels"][0],
                        "start": v["start"],
                        "end": v["end"],
                    }
                )
        gold[doc_id] = spans
    return gold, texts


def load_config(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def to_nervaluate(spans: list) -> list:
    """Format entity spans for strict and partial overlap evaluation.

    The evaluator resolves overlapping predictions greedily against
    gold spans. Ordering by start ascending and length descending
    prioritizes exact boundaries before evaluating partial sub-spans.
    """
    spans = sorted(spans, key=lambda s: (s["start"], -(s["end"] - s["start"])))
    return [{"label": s["label"], "start": s["start"], "end": s["end"]} for s in spans]


def evaluate(gold: dict, configs: dict) -> dict:
    """Compute strict and partial SemEval metrics for each configuration.

    Models target distinct subsets of entity types (e.g. dictionary
    matching excludes location and disease, while LLM extracts them).
    Gold annotations and system predictions are restricted to each
    configuration's supported tag set to avoid penalizing out-of-scope
    types.
    """
    from nervaluate import Evaluator

    results = {}

    for cfg_name, cfg_data in configs.items():
        cfg_types = CONFIG_TYPES.get(cfg_name, ALL_TYPES)
        type_set = set(cfg_types)

        doc_ids = sorted(set(gold) & set(cfg_data))
        if not doc_ids:
            print(f"\n  {cfg_name} -- no overlapping docs with gold, skipping")
            continue
        print(f"\n  {cfg_name} -- evaluating on {len(doc_ids)} docs")

        y_true = [to_nervaluate([s for s in gold[d] if s["label"] in type_set]) for d in doc_ids]
        y_pred = [
            to_nervaluate([s for s in cfg_data.get(d, []) if s["label"] in type_set])
            for d in doc_ids
        ]

        evaluator = Evaluator(y_true, y_pred, tags=cfg_types)
        result = evaluator.evaluate()

        overall = result["overall"]
        per_tag = result["entities"]

        s = overall["strict"]
        pt = overall["partial"]

        per_type_f1 = {}
        for ent in cfg_types:
            tag = per_tag.get(ent, {}).get("strict")
            f1 = tag.f1 if tag else 0.0
            per_type_f1[ent] = f1

        # Exclude types absent from gold data so that uninstantiated
        # classes do not skew macro averages downward.
        gold_types_with_data = set()
        for d in doc_ids:
            for gs in gold[d]:
                gold_types_with_data.add(gs["label"])
        macro_f1s = [f1 for ent, f1 in per_type_f1.items() if ent in gold_types_with_data]
        macro_f1 = sum(macro_f1s) / len(macro_f1s) if macro_f1s else 0.0
        status = "  (excludes zero-gold types)" if len(macro_f1s) < len(per_type_f1) else ""

        print(f"\n{'=' * 55}")
        print(f"  {cfg_name}")
        print(f"{'=' * 55}")
        print(f"  Micro-avg Strict  P: {s.precision:.4f}  R: {s.recall:.4f}  F1: {s.f1:.4f}")
        print(f"  Macro-avg Strict  F1: {macro_f1:.4f} {status}")
        print(f"  Partial           P: {pt.precision:.4f}  R: {pt.recall:.4f}  F1: {pt.f1:.4f}")
        print("\n  Per entity type -- Strict F1:")
        for ent in cfg_types:
            f1 = per_type_f1[ent]
            bar = "#" * int(f1 * 25)
            flag = "  <- low" if f1 < 0.3 and f1 > 0 else ""
            print(f"    {ent:26s} {f1:.4f}  {bar}{flag}")

        results[cfg_name] = {
            "strict_P": s.precision,
            "strict_R": s.recall,
            "strict_F1": s.f1,
            "macro_F1": macro_f1,
            "partial_P": pt.precision,
            "partial_R": pt.recall,
            "partial_F1": pt.f1,
            "per_type": per_type_f1,
        }

    return results


def save_csv(results: dict, base_path: str | None = None):
    if base_path is None:
        base_path = str(_RESULTS_DIR / "results.csv")
    os.makedirs(os.path.dirname(base_path), exist_ok=True)

    with open(base_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "Config",
                "Micro_P",
                "Micro_R",
                "Micro_F1",
                "Macro_F1",
                "Partial_P",
                "Partial_R",
                "Partial_F1",
            ]
        )
        for cfg, r in results.items():
            w.writerow(
                [
                    cfg,
                    f"{r['strict_P']:.4f}",
                    f"{r['strict_R']:.4f}",
                    f"{r['strict_F1']:.4f}",
                    f"{r['macro_F1']:.4f}",
                    f"{r['partial_P']:.4f}",
                    f"{r['partial_R']:.4f}",
                    f"{r['partial_F1']:.4f}",
                ]
            )
    print(f"\n  Saved {base_path}")

    per_type_path = base_path.replace(".csv", "_per_type.csv")
    with open(per_type_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Entity_Type"] + list(results.keys()))
        for ent in ALL_TYPES:
            row = [ent] + [f"{results[cfg]['per_type'].get(ent, 0):.4f}" for cfg in results]
            w.writerow(row)
    print(f"  Saved {per_type_path}")


if __name__ == "__main__":
    print("=" * 55)
    print("  Evaluate -- NER Configurations")
    print("=" * 55)

    print(f"\nLoading gold standard from {GOLD_PATH}...")
    gold, texts = load_gold(GOLD_PATH)
    total_gold = sum(len(v) for v in gold.values())
    print(f"  {len(gold)} documents  |  {total_gold} gold entities")

    print("\nChecking config files...")
    for cfg_name, path in CONFIGS.items():
        exists = os.path.exists(path)
        total = sum(len(v) for v in load_config(path).values()) if exists else 0
        status = f"{total} entities" if exists else "NOT FOUND -- run extract.py first"
        print(f"  {'[OK]' if exists else '[MISS]'} {path:35s} {status}")

    loaded_configs = {}
    for cfg_name, path in CONFIGS.items():
        if os.path.exists(path):
            loaded_configs[cfg_name] = load_config(path)
        else:
            print(f"  Skipping {cfg_name} -- {path} not found")

    print("\nEvaluating with nervaluate...")
    results = evaluate(gold, loaded_configs)

    save_csv(results)

    print()
    print("=" * 55)
    print("  SUMMARY")
    print("=" * 55)
    for cfg, r in results.items():
        mibar = "#" * int(r["strict_F1"] * 30)
        mabar = "#" * int(r["macro_F1"] * 30) if r["macro_F1"] else "N/A"
        print(f"  {cfg:32s} Micro-F1: {r['strict_F1']:.4f}  {mibar}")
        print(f"  {'':32s} Macro-F1: {r['macro_F1']:.4f}  {mabar}")

    print()
    print("  Copy to paper from:")
    print(f"    {_RESULTS_DIR / 'results.csv'}")
    print(f"    {_RESULTS_DIR / 'results_per_type.csv'}")
