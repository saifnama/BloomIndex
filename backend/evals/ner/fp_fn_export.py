"""Export error breakdowns between gold annotations and model predictions.

Isolates false positives and false negatives with surrounding text
context to facilitate qualitative error analysis across pipeline
configurations.
"""

import csv
import json
from collections import defaultdict
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_DATA_DIR = _SCRIPT_DIR / "data"
_RESULTS_DIR = _SCRIPT_DIR / "results"

GOLD_PATH = _DATA_DIR / "annotated_data.json"
CONFIG_PATHS = {
    "Dictionary": _DATA_DIR / "dictionary.json",
    "LLM": _DATA_DIR / "llm.json",
    "Hybrid": _DATA_DIR / "hybrid.json",
}


def load_gold(path):
    """Load evaluation ground truth from annotated data export."""
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
                        "text": v["text"],
                        "label": v["labels"][0],
                        "start": v["start"],
                        "end": v["end"],
                    }
                )
        gold[doc_id] = spans
    return gold, texts


def load_config(path):
    with open(path) as f:
        return json.load(f)


def find_fp_fn(gold_doc, pred_doc, gold_text):
    """Identify exact-boundary false positives and false negatives.

    Uses (start, end, label) coordinate tuples to partition predictions
    into strict matches, unpredicted gold spans (false negatives), and
    unsupported predictions (false positives). Slices localized text
    windows around each span to preserve context for qualitative review.
    """
    gold_set = set()
    gold_by_offset = {}
    for g in gold_doc:
        key = (g["start"], g["end"], g["label"])
        gold_set.add(key)
        gold_by_offset[key] = g

    pred_set = set()
    pred_list = []
    for p in pred_doc:
        key = (p["start"], p["end"], p["label"])
        pred_set.add(key)
        pred_list.append(p)

    fps = []
    for p in pred_list:
        key = (p["start"], p["end"], p["label"])
        if key not in gold_set:
            ctx_start = max(0, p["start"] - 40)
            ctx_end = min(len(gold_text), p["end"] + 40)
            fps.append(
                {
                    "text": p["text"],
                    "label": p["label"],
                    "start": p["start"],
                    "end": p["end"],
                    "context": gold_text[ctx_start:ctx_end],
                }
            )

    fns = []
    for g in gold_doc:
        key = (g["start"], g["end"], g["label"])
        if key not in pred_set:
            ctx_start = max(0, g["start"] - 40)
            ctx_end = min(len(gold_text), g["end"] + 40)
            fns.append(
                {
                    "text": g["text"],
                    "label": g["label"],
                    "start": g["start"],
                    "end": g["end"],
                    "context": gold_text[ctx_start:ctx_end],
                }
            )

    return fps, fns


def main():
    print("=" * 55)
    print("  FP/FN Export")
    print("=" * 55)

    gold, texts = load_gold(GOLD_PATH)
    print(f"Loaded {len(gold)} gold documents\n")

    for cfg_name, cfg_path in CONFIG_PATHS.items():
        if not cfg_path.exists():
            print(f"[SKIP] {cfg_name} — {cfg_path} not found")
            continue

        pred = load_config(cfg_path)
        all_fps = []
        all_fns = []
        fp_by_type = defaultdict(list)
        fn_by_type = defaultdict(list)

        for doc_id in gold:
            if doc_id not in pred:
                continue
            g_doc = gold[doc_id]
            p_doc = pred[doc_id]
            fps, fns = find_fp_fn(g_doc, p_doc, texts.get(doc_id, ""))
            all_fps.extend(fps)
            all_fns.extend(fns)

            for fp in fps:
                fp_by_type[fp["label"]].append(fp)
            for fn in fns:
                fn_by_type[fn["label"]].append(fn)

        print(f"\n{'=' * 55}")
        print(f"  {cfg_name}")
        print(f"{'=' * 55}")
        print(f"  Total FPs: {len(all_fps)}  Total FNs: {len(all_fns)}")

        print("\n  FPs by type:")
        for t, items in sorted(fp_by_type.items(), key=lambda x: -len(x[1])):
            print(f"    {t:26s} {len(items):5d}")

        print("\n  FNs by type:")
        for t, items in sorted(fn_by_type.items(), key=lambda x: -len(x[1])):
            print(f"    {t:26s} {len(items):5d}")

        print("\n  --- Top FP Examples ---")
        for fp in sorted(all_fps, key=lambda x: x["start"])[:10]:
            cat = "hallucination / wrong substring"
            print(f"    [{fp['label']:20s}] {fp['text']!r:30s} ctx: ...{fp['context']}...")

        print("\n  --- Top FN Examples ---")
        for fn in sorted(all_fns, key=lambda x: x["start"])[:10]:
            print(f"    [{fn['label']:20s}] {fn['text']!r:30s} ctx: ...{fn['context']}...")

    print("\n\nSaving detailed FP/FN exports...")
    for cfg_name, label in [("Dictionary", "dict"), ("Hybrid", "hybrid")]:
        cfg_path = CONFIG_PATHS[cfg_name]
        if not cfg_path.exists():
            continue
        pred = load_config(cfg_path)

        with open(_RESULTS_DIR / f"fp_fn_{label}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["doc_id", "type", "mode", "text", "start", "end", "context", "category"])

            for doc_id in gold:
                if doc_id not in pred:
                    continue
                fps, fns = find_fp_fn(gold[doc_id], pred[doc_id], texts.get(doc_id, ""))

                for fp in fps:
                    w.writerow(
                        [
                            doc_id,
                            fp["label"],
                            "FP",
                            fp["text"],
                            fp["start"],
                            fp["end"],
                            fp["context"],
                            "",
                        ]
                    )
                for fn in fns:
                    w.writerow(
                        [
                            doc_id,
                            fn["label"],
                            "FN",
                            fn["text"],
                            fn["start"],
                            fn["end"],
                            fn["context"],
                            "",
                        ]
                    )

        print(f"  Saved {_RESULTS_DIR / 'fp_fn_' + label + '.csv'}")

    print("\nDone.")


if __name__ == "__main__":
    main()
