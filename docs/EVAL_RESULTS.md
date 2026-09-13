# Evaluation Results

This document reports the stored evaluation results for BloomIndex.

The NER numbers come from a full run on the gold standard. The RAG numbers come from a small smoke run and show harness behavior, not benchmark quality.

---

# NER Benchmark Size

| Item | Count |
|------|-------|
| Documents | 165 |
| Gold entities | 4,084 |
| Entity types | 10 |
| Entities per document | 24.75 |

---

# NER Overall Results

Strict SemEval-2013 scores, computed with `nervaluate`:

| Config | Micro Precision | Micro Recall | Micro F1 | Macro F1 |
|--------|-----------------|--------------|----------|----------|
| Dictionary | 0.9826 | 0.9864 | **0.9845** | 0.9850 |
| LLM only | 0.7229 | 0.7481 | 0.7352 | 0.6887 |
| Hybrid | 0.7735 | **0.9106** | 0.8365 | 0.8630 |

Partial-match F1 softens the gap: dictionary 0.9864, LLM 0.7822, hybrid 0.8682.

---

# NER Results per Entity Type

Strict F1 per type:

| Entity Type | Dictionary | LLM | Hybrid |
|-------------|------------|-----|--------|
| Development Stage | 1.0000 | 0.0000 | 1.0000 |
| Plant Part | 0.9977 | 0.0000 | 0.9977 |
| Analytical Technique | 0.9902 | 0.0000 | 0.9902 |
| Bioactivity | 0.9896 | 0.6295 | 0.7690 |
| Species | 0.9821 | 0.5398 | 0.6247 |
| Chemical | 0.9813 | 0.8888 | 0.9318 |
| Extraction Method | 0.9756 | 0.0000 | 0.9756 |
| Season | 0.9638 | 0.0000 | 0.9638 |
| Location | 0.0000 | 0.7602 | 0.7526 |
| Disease | 0.0000 | 0.6250 | 0.6250 |

Reading of the table:

* The dictionary scores above 0.96 on all eight of its types.
* The LLM is the only source for Location and Disease. The dictionaries do not cover them by design.
* The LLM prompt excludes the five structural types on purpose, so its LLM-only scores are 0.0 there.
* Hybrid trades precision for recall: it lowers F1 on dictionary-covered types and adds the two context types.

---

# Error Summary

The dictionary makes few errors: 64 false positives and 463 false negatives on the gold set. Most come from context-blind substring matches and gazetteer gaps.

The LLM makes more: 815 false positives, mostly wrong labels such as plant families marked as species. The verbatim filter rejects invented text, so only 2 of 3,085 LLM entities were pure hallucinations.

Hybrid lowers false negatives from 463 to 135 but raises false positives to 859.

---

# NER Runtime

Measured per 300-word abstract:

| Config | Latency | Throughput |
|--------|---------|------------|
| Dictionary only | about 1 ms | about 970 abstracts per second |
| LLM only | about 5 s | 12 abstracts per minute |
| Hybrid | about 5 s | 12 abstracts per minute |

The dictionary scan takes about 0.2 ms per abstract and the fusion step under 1 ms. The LLM call takes 2 to 10 seconds and dominates every hybrid run.

Three parallel LLM workers raise extraction throughput from 12 to 36 abstracts per minute. A 10,000-abstract corpus takes about 10 seconds with dictionaries alone and about 14 hours with the LLM.

---

# RAG Smoke-Run Results

| Metric | Score |
|--------|-------|
| Faithfulness | 1.000 |
| Answer Relevancy | 0.881 |
| Answer Correctness | 0.820 |
| Context Precision | 0.964 |
| Context Recall | 1.000 |

---

# Observations

## Strengths

* Near-perfect dictionary precision and recall on its eight types
* Hybrid recall of 0.91, with coverage of location and disease
* Near-zero LLM hallucination after the verbatim filter
* Dictionary-only mode costs about 1 ms per abstract

## Areas for Improvement

* Hybrid precision drops to 0.77
* The RAG benchmark is smoke-scale and needs a full question bank
