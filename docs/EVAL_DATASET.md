# Evaluation Dataset

This document describes the datasets used to evaluate BloomIndex.

BloomIndex has two evaluation datasets. The NER gold set measures entity extraction. The RAG question bank measures retrieval and answer generation.

---

# NER Gold Standard

The gold set is annotated plant-science text. It is stored at `backend/evals/ner/data/annotated_data.json`.

## Gold Set Size

```text
165 Documents

4,084 Gold Entities

24.75 Entities per Document
```

## Entity Type Breakdown

The gold set has ten entity types. Eight come from the dictionaries. LOCATION and DISEASE are context types that only the LLM pass produces.

| Entity Type | Count | Documents with Type |
|-------------|-------|---------------------|
| Chemical | 1,415 | 156 |
| Species | 862 | 160 |
| Plant Part | 426 | 111 |
| Location | 391 | 128 |
| Analytical Technique | 354 | 145 |
| Bioactivity | 291 | 68 |
| Season | 141 | 46 |
| Extraction Method | 100 | 72 |
| Development Stage | 82 | 29 |
| Disease | 22 | 13 |

## Dictionary Sizes

The eight dictionaries hold these term counts:

| Dictionary | Terms |
|------------|-------|
| Species | 236,575 |
| Chemical | 68,939 |
| Plant Part | 198 |
| Analytical Technique | 139 |
| Bioactivity | 83 |
| Extraction Method | 37 |
| Season | 34 |
| Development Stage | 23 |

Counts are CSV records. Chemical records also carry synonyms, so the matcher knows more surface forms than the count shows.

---

## Prediction Files

`extract.py` writes one prediction file per configuration:

```text
backend/evals/ner/data/

annotated_data.json    gold standard
dictionary.json        dictionary-only spans
llm.json               LLM-only spans
hybrid.json            merged spans (union + deduplication)
```

---

# RAG Question Bank

The RAG benchmark lives at `backend/evals/rag/data/`. Questions are grounded in the papers indexed by `scripts/ingest_kb.py`.

Two question formats exist.

## Open-Ended Questions

File: `data/open_ended.csv`

```csv
id,question,reference,difficulty
```

| Field | Description |
|-------|-------------|
| id | Unique question identifier |
| question | Evaluation question |
| reference | Ground-truth answer |
| difficulty | easy, medium, or hard |

## Multiple Choice Questions

File: `data/mcq.csv`

```csv
id,question,option_a,option_b,option_c,option_d,correct_option,difficulty
```

| Field | Description |
|-------|-------------|
| option_a - option_d | Candidate answers |
| correct_option | Ground-truth option letter |
| difficulty | easy, medium, or hard |

JSON question sets can be converted to CSV with `scripts/json_to_csv.py`.

## Current Bank Size

```text
50 Open-Ended Questions

90 MCQs
```

The stored RAG numbers are a smoke-scale run. They check that the harness works end to end. They are not a full benchmark. The current questions do not carry difficulty labels yet.

---

# Why Ground Questions in Source Papers?

* Questions stay grounded in real literature.
* Ground-truth answers come from the same papers.
* You can measure retrieval quality against a known source.

---

# Dataset Limitations

* One annotator produced the NER gold set.
* The question bank is small and LLM-generated.
* Questions do not yet cover multi-document reasoning.

---
