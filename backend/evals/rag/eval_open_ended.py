"""Evaluate open-ended RAG answers using standard RAGAS metrics.

Computes faithfulness, answer relevancy, answer correctness, context
precision, and context recall over retrieved contexts and generated
responses.
"""

import ast
import json
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd
from ragas.embeddings.base import embedding_factory
from ragas.llms import llm_factory
from ragas.metrics.collections import (
    AnswerCorrectness,
    AnswerRelevancy,
    ContextPrecision,
    ContextRecall,
    Faithfulness,
)

OUTPUT_DIR = Path(os.getenv("EVAL_OUTPUT_DIR", "backend/evals/rag/results"))
INPUT_CSV = OUTPUT_DIR / "rag_outputs_open_ended.csv"
OUTPUT_CSV = OUTPUT_DIR / "open_ended_eval_results.csv"
CHECKPOINT = OUTPUT_DIR / ".checkpoints/open_ended_eval_checkpoint.json"

OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)

METRIC_MAX_RETRIES = int(os.getenv("METRIC_MAX_RETRIES", "3"))
METRIC_BASE_DELAY = float(os.getenv("METRIC_BASE_DELAY", "30"))


def _is_transient_error(exc: Exception) -> bool:
    """Identify recoverable network, timeout, or rate-limit exceptions.

    Prevents transient server disconnects and HTTP rate throttling from
    failing long-running evaluation passes immediately.
    """
    from openai import (
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
        RateLimitError,
    )

    if isinstance(exc, (APIConnectionError, APITimeoutError, InternalServerError, RateLimitError)):
        return True
    try:
        from instructor.v2.core.errors import InstructorRetryException

        if isinstance(exc, InstructorRetryException):
            return True
    except ImportError:
        pass
    msg = str(exc).lower()
    return any(
        kw in msg for kw in ("524", "502", "503", "504", "timeout", "timed out", "connection")
    )


def score_with_retry(score_fn, metric_name: str, max_retries: int = METRIC_MAX_RETRIES):
    """Execute evaluation scoring with exponential backoff.

    Applies progressive backoff intervals to absorb remote endpoint
    overloads before marking a sample metric as uncomputable.
    """
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            return score_fn()
        except Exception as e:
            last_exc = e
            if attempt < max_retries and _is_transient_error(e):
                delay = METRIC_BASE_DELAY * (2 ** (attempt - 1))
                print(
                    f"  {metric_name} attempt {attempt}/{max_retries} failed (transient), retrying in {delay:.0f}s: {e}"
                )
                time.sleep(delay)
            else:
                break
    raise last_exc


def load_checkpoint() -> int:
    if CHECKPOINT.exists():
        return json.loads(CHECKPOINT.read_text()).get("last_index", -1)
    return -1


def save_checkpoint(idx: int):
    CHECKPOINT.write_text(json.dumps({"last_index": idx}))


def append_row(row: dict):
    df = pd.DataFrame([row])
    df.to_csv(
        OUTPUT_CSV,
        mode="a",
        header=not OUTPUT_CSV.exists(),
        index=False,
        quoting=1,
        escapechar="\\",
    )


def _get_llm_client_and_model():
    from backend.src.common.llm_client import create_ragas_client

    return create_ragas_client()


if __name__ == "__main__":
    df = pd.read_csv(INPUT_CSV)
    print(f"Loaded {len(df)} generated RAG samples")

    start_idx = load_checkpoint()
    print(f"Resuming from index {start_idx + 1}")

    local_client, eval_model = _get_llm_client_and_model()

    eval_llm = llm_factory(
        model=eval_model,
        provider="openai",
        client=local_client,
        max_tokens=4096,
        temperature=0,
    )

    print(f"Using {eval_llm} for Eval")

    embeddings = embedding_factory(
        "huggingface",
        model=os.getenv(
            "RAGAS_EMBEDDING_MODEL", os.getenv("RAG_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
        ),
    )

    faithfulness = Faithfulness(llm=eval_llm)
    relevancy = AnswerRelevancy(llm=eval_llm, embeddings=embeddings)
    correctness = AnswerCorrectness(llm=eval_llm, embeddings=embeddings)
    ctx_precision = ContextPrecision(llm=eval_llm)
    ctx_recall = ContextRecall(llm=eval_llm)

    for idx, row in df.iterrows():
        if idx <= start_idx:
            continue

        print(f"Evaluating {idx + 1}/{len(df)}")

        error_msg = None
        contexts = None
        answer = row.get("answer")

        try:
            if pd.notna(row.get("contexts")) and str(row["contexts"]).strip():
                contexts = ast.literal_eval(row["contexts"])
                if not isinstance(contexts, list) or len(contexts) == 0:
                    contexts = None
            else:
                contexts = None
        except Exception:
            contexts = None

        if contexts is None:
            error_msg = "retrieved_contexts is missing"

        if pd.isna(answer) or not str(answer).strip():
            error_msg = (
                f"{error_msg}; generated_answer is missing"
                if error_msg
                else "generated_answer is missing"
            )

        if error_msg:
            append_row(
                {
                    "id": row.get("id"),
                    "question": row.get("question"),
                    "faithfulness": np.nan,
                    "answer_relevancy": np.nan,
                    "answer_correctness": np.nan,
                    "context_precision": np.nan,
                    "context_recall": np.nan,
                    "error": error_msg,
                }
            )
            save_checkpoint(idx)
            print(f"  Row {idx}: {error_msg}")
            continue

        reference = row.get("reference")
        metrics_result = {}

        metric_defs = [
            (
                "faithfulness",
                lambda: (
                    faithfulness.score(
                        user_input=row["question"],
                        response=answer,
                        retrieved_contexts=contexts,
                    ).value
                ),
            ),
            (
                "answer_relevancy",
                lambda: (
                    relevancy.score(
                        user_input=row["question"],
                        response=answer,
                    ).value
                ),
            ),
            (
                "answer_correctness",
                lambda: (
                    correctness.score(
                        user_input=row["question"],
                        response=answer,
                        reference=reference,
                    ).value
                ),
            ),
            (
                "context_precision",
                lambda: (
                    ctx_precision.score(
                        user_input=row["question"],
                        reference=reference,
                        retrieved_contexts=contexts,
                    ).value
                ),
            ),
            (
                "context_recall",
                lambda: (
                    ctx_recall.score(
                        user_input=row["question"],
                        reference=reference,
                        retrieved_contexts=contexts,
                    ).value
                ),
            ),
        ]

        for metric_name, score_fn in metric_defs:
            try:
                metrics_result[metric_name] = score_with_retry(score_fn, metric_name)
            except Exception as e:
                metrics_result[metric_name] = np.nan
                print(f"  {metric_name} failed after retries: {e}")

        has_errors = any(np.isnan(v) for v in metrics_result.values())
        result = {
            "id": row.get("id"),
            "question": row["question"],
            "answer": answer,
            "reference": reference,
            "contexts": row.get("contexts"),
            **metrics_result,
            "error": "partial" if has_errors else None,
        }

        append_row(result)
        save_checkpoint(idx)

    if OUTPUT_CSV.exists():
        out_df = pd.read_csv(OUTPUT_CSV, engine="python", on_bad_lines="skip")

        total = len(out_df)
        failed = out_df["error"].notna().sum()
        success = total - failed

        print("\nOpen-ended RAGAS evaluation complete\n")
        print("Evaluation Summary")
        print("---------------------")
        print(f"Total samples        : {total}")
        print(f"Successfully scored  : {success}")
        print(f"Failed samples       : {failed}")
        print(f"Success rate         : {(success / total) * 100:.2f}%")

        valid_df = out_df[out_df["error"].isna()]
        if not valid_df.empty:
            print("\nAverage Scores (valid samples only):")
            print(
                valid_df[
                    [
                        "faithfulness",
                        "answer_relevancy",
                        "answer_correctness",
                        "context_precision",
                        "context_recall",
                    ]
                ].mean()
            )
        else:
            print("\nNo valid samples available for averaging")

        print("\nFailure breakdown:")
        print(out_df["error"].value_counts(dropna=True))
    else:
        print("No evaluation output found (CSV not created)")
