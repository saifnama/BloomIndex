# Evaluation Methodology

This document describes the evaluation methodology for BloomIndex.

The goal is to measure how well BloomIndex extracts entities from plant-science text and generates literature-grounded answers.

---

# Evaluation Overview

BloomIndex has two core pipelines. Each pipeline has its own evaluation framework:

1. NER evaluation against an annotated gold standard
2. Chat evaluation with MCQ accuracy and RAGAS metrics

A correct chat answer depends on two steps. The system must retrieve the right passages. It must then generate an answer that the passages support. The evaluation framework measures both steps.

---

# Evaluation Workflow

```text
Papers
    ↓
Gold-Standard Annotation / Question Bank
    ↓
Extraction (Dictionary, LLM, Hybrid) / RAG Answers
    ↓
SemEval-2013 Scoring / MCQ + RAGAS Scoring
    ↓
Aggregation
    ↓
Error Analysis
```

---

# NER Evaluation

The NER evaluation compares three extraction configurations on the same gold set:

* Dictionary — eight gazetteer matchers only
* LLM — one LLM extraction pass only
* Hybrid — dictionary spans plus LLM spans, merged and deduplicated

Scoring uses the `nervaluate` package with the SemEval-2013 scheme. The scheme reports strict and partial scores.

```text
Strict match: same type and same character offsets
Partial match: overlapping text, but the offsets or the type differ
```

The headline metrics are micro precision, micro recall, and micro F1.

---

# Chat Evaluation

The chat evaluation runs benchmark questions through the real RAG pipeline. The same retrieval, reranking, prompting, and generation code serves both chat users and the evaluation.

## MCQ Evaluation

Each MCQ goes through the full RAG pipeline. The evaluation parses the predicted option from the answer and compares it with the ground truth.

```text
Accuracy = Correct Answers / Total Questions
```

## Open-Ended Evaluation

Each open-ended question goes through the same pipeline. The stored outputs are:

* Generated answer
* Retrieved contexts
* Retrieved sources

RAGAS scores each answer against the reference.

---

# RAGAS Metrics

## Faithfulness

Measures whether the answer is supported by the retrieved context.

## Answer Relevancy

Measures how well the answer addresses the question.

## Answer Correctness

Measures semantic similarity between the generated answer and the reference answer.

## Context Precision

Measures how much of the retrieved context is useful for the answer.

## Context Recall

Measures whether the retrieved context contains the information the question needs.

---

# Evaluation Model

RAGAS and the LLM extraction pass use the same shared `LLM_API_*` client as the application. You configure one endpoint, one key, and one model. There is no separate judge configuration.

RAGAS embeddings use `BAAI/bge-small-en-v1.5` by default. The `RAGAS_EMBEDDING_MODEL` variable changes it.

---

# Result Aggregation

The NER harness writes per-configuration and per-type scores to CSV files. The chat harness writes per-question scores and prints aggregate averages.

Aggregate reports include:

* Micro and macro F1 per config
* F1 per entity type
* RAGAS averages
* MCQ accuracy by difficulty

---

# Error Analysis

The NER harness exports false positives and false negatives per configuration (`fp_fn_export.py`). The error analysis groups findings into categories:

* Wrong substring matches (dictionary)
* Wrong labels, such as plant families marked as species (LLM)
* Hallucinated spans (LLM)
* Boundary errors that count as both FP and FN under strict scoring
* Missing gazetteer entries (dictionary)

These categories guide fixes to the dictionaries, the prompts, and the fusion step.

---

# Evaluation Limitations

* The gold set covers 165 abstract-length texts.
* The current RAG question bank is a small smoke set, not a full benchmark.
* MCQ results are pending until the MCQ bank is generated.
* RAGAS scores depend on the quality of the configured LLM.
* Questions are LLM-generated, not expert-validated.

Interpret the numbers with these limits in mind.

---

# Future Improvements

* Human expert review of the gold set
* A full-scale MCQ and open-ended benchmark
* Retrieval ranking metrics such as Hit@K and MRR
* Citation accuracy checks for `[cN]` markers
