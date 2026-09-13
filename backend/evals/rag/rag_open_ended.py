"""Generate answers for open-ended benchmark questions via RAG.

Executes retrieval-augmented generation against open-ended evaluation
prompts, recording generated answers, retrieved chunk contexts, and
source provenance for downstream RAGAS metric scoring.
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

OUTPUT_DIR = Path(os.getenv("EVAL_OUTPUT_DIR", "backend/evals/rag/results"))
INPUT_CSV = Path(os.getenv("EVAL_OPEN_ENDED_CSV", "backend/evals/rag/data/open_ended.csv"))
OUTPUT_CSV = OUTPUT_DIR / "rag_outputs_open_ended.csv"
CHECKPOINT = OUTPUT_DIR / ".checkpoints/rag_open_ended_checkpoint.json"

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


async def run_single_query(question: str) -> dict[str, Any]:
    from backend.src.chat.service import get_rag_service

    service = get_rag_service()
    result = await service.query(question=question, user_id="eval")

    answer = result.get("answer", "")
    sources_raw = result.get("sources", [])
    contexts = [s["chunk_text"] for s in sources_raw if s.get("chunk_text")]
    sources, similarity_score = extract_sources_and_similarity(sources_raw)

    return {
        "answer": answer,
        "contexts": contexts,
        "sources": sources,
        "similarity_score": similarity_score,
    }


async def main():
    df = pd.read_csv(INPUT_CSV)
    print(f"Loaded {len(df)} open-ended questions")

    start_idx = load_checkpoint()
    print(f"Resuming from index {start_idx + 1}")

    for idx, row in df.iterrows():
        if idx <= start_idx:
            continue

        question = row["question"]
        print(f"Running question {idx + 1}/{len(df)}")

        try:
            rag_result = await run_single_query(question)

            output_row = {
                "id": row.get("id", idx + 1),
                "question": question,
                "reference": row.get("reference"),
                "answer": rag_result["answer"],
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

    print(f"Open-ended RAG generation completed -> {OUTPUT_CSV}")


if __name__ == "__main__":
    asyncio.run(main())
