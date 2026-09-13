"""Generate answers for multiple-choice benchmark questions via RAG.

Retrieves candidate chunks for structured MCQ prompts, executes
inference with answer format constraints, and records output contexts
and source metadata for accuracy assessment.
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

OUTPUT_DIR = Path(os.getenv("EVAL_OUTPUT_DIR", "backend/evals/rag/results"))
INPUT_CSV = Path(os.getenv("EVAL_MCQ_CSV", "backend/evals/rag/data/mcq.csv"))
OUTPUT_CSV = OUTPUT_DIR / "rag_outputs_mcq.csv"
CHECKPOINT = OUTPUT_DIR / ".checkpoints/rag_mcq_checkpoint.json"

OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)


def load_checkpoint() -> int:
    if CHECKPOINT.exists():
        return json.loads(CHECKPOINT.read_text()).get("last_index", -1)
    return -1


def save_checkpoint(idx: int):
    CHECKPOINT.write_text(json.dumps({"last_index": idx}))


def append_row(row: dict[str, Any]):
    df = pd.DataFrame([row])
    df.to_csv(OUTPUT_CSV, mode="a", header=not OUTPUT_CSV.exists(), index=False)


def build_mcq_query(row: pd.Series) -> str:
    """Format multiple-choice items with strict output schema guidance.

    Instructs the generator to isolate the option letter on a dedicated
    line to facilitate reliable regex extraction during evaluation.
    """
    return f"""
Question:
{row["question"]}

Options:
A. {row["option_a"]}
B. {row["option_b"]}
C. {row["option_c"]}
D. {row["option_d"]}

Answer format STRICTLY:
Option: A/B/C/D
Explanation: short justification based on context
""".strip()


def extract_sources_and_similarity(sources):
    """Isolate unique source filenames and peak chunk similarity.

    Deduplicates overlapping chunk references to file-level provenance
    and tracks maximum retrieval similarity as a metric of context
    relevance.
    """
    if not sources:
        return [], None
    filenames = set()
    similarities = []
    for src in sources:
        if src.get("filename"):
            filenames.add(src["filename"])
        if isinstance(src.get("similarity"), (int, float)):
            similarities.append(src["similarity"])
    return sorted(filenames), max(similarities) if similarities else None


async def run_single_mcq(row: pd.Series) -> dict[str, Any]:
    from backend.src.chat.service import get_rag_service

    service = get_rag_service()
    query = build_mcq_query(row)
    result = await service.query(question=query, user_id="eval")

    answer_text = result.get("answer", "")
    sources_raw = result.get("sources", [])
    contexts = [s["chunk_text"] for s in sources_raw if s.get("chunk_text")]
    sources, similarity_score = extract_sources_and_similarity(sources_raw)

    return {
        "model_answer": answer_text,
        "contexts": contexts,
        "sources": sources,
        "similarity_score": similarity_score,
    }


async def main():
    df = pd.read_csv(INPUT_CSV)
    print(f"Loaded {len(df)} MCQ questions")

    start_idx = load_checkpoint()
    print(f"Resuming from index {start_idx + 1}")

    for idx, row in df.iterrows():
        if idx <= start_idx:
            continue

        print(f"Running MCQ {idx + 1}/{len(df)}")

        try:
            rag_result = await run_single_mcq(row)

            output_row = {
                "id": row.get("id", idx + 1),
                "question": row["question"],
                "option_a": row["option_a"],
                "option_b": row["option_b"],
                "option_c": row["option_c"],
                "option_d": row["option_d"],
                "correct_option": row["correct_option"],
                "model_answer": rag_result["model_answer"],
                "contexts": rag_result["contexts"],
                "sources": rag_result["sources"],
                "similarity_score": rag_result["similarity_score"],
                "difficulty": row.get("difficulty"),
            }

            append_row(output_row)
            save_checkpoint(idx)

        except Exception as e:
            print(f"Stopped safely at row {idx}: {e}")
            break

    print(f"MCQ RAG generation completed -> {OUTPUT_CSV}")


if __name__ == "__main__":
    asyncio.run(main())
