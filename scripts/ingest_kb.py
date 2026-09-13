"""Standalone ingestion pipeline for the permanent paper knowledge base.

Usage:
    python ingest.py           # process everything pending
    python ingest.py --status  # print catalog summary, exit

Delete a paper:
    python ingest.py --delete  (interactive)

Processes raw PDF papers into markdown, extracts document metadata,
generates parent-child chunk hierarchies, and indexes dense and sparse
vectors into Qdrant. Manages state in a dedicated SQLite database to
support idempotent re-runs, content-hash cache invalidation, and
interactive paper deletion.
"""

import argparse
import difflib
import glob
import hashlib
import logging
import multiprocessing
import os
import re
import sqlite3
import sys
import time
import traceback
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

# Add repository root to sys.path for standalone script execution.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Configuration
RAW_DIR = os.path.join(_REPO_ROOT, "knowledge_base/papers")
MARKDOWN_DIR = os.path.join(_REPO_ROOT, "knowledge_base/parsed")
STATE_DB = os.path.join(_REPO_ROOT, "knowledge_base/kb.sqlite")

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION_NAME = "kb_papers"
DENSE_DIM = 384
DENSE_VEC = "dense"
SPARSE_VEC = "sparse"
SPARSE_MODEL = "Qdrant/bm25"

EMBEDDING_MODEL_NAME = "BAAI/bge-small-en-v1.5"
PARENT_MAX_TOKENS = 500
CHILD_CHUNK_CHARS = 900
CHILD_CHUNK_OVERLAP = 120

# Restrict to one worker unless multiple GPUs are available to prevent
# CUDA memory and context thrashing.
PARSE_WORKERS = 1
GPU_PAGE_BATCH = 8


def _detect_device() -> str:
    """Return 'cuda', 'mps', or 'cpu' depending on what's available."""
    try:
        import torch

        if torch.cuda.is_available() and torch.cuda.device_count() > 0:
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        pass
    return "cpu"


# Constant namespace ensures deterministic UUID5 generation across runs,
# preventing duplicate vector records on re-indexing.
ID_NAMESPACE = uuid.UUID("a4f1b9d2-7e3a-4c8b-9f10-7c5d2e8a1b00")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("kb_ingest")

DOI_PATTERN = re.compile(
    r"\b(https?://doi\.org/|doi:\s*)?(10\.\d{4,9}/[^\s\"'<>)]+)",
    re.IGNORECASE,
)


def _build_noise_labels():
    """Build the set of DocItemLabel values to exclude from indexing.

    Imported lazily to avoid loading docling at module import time in
    processes that do not perform PDF parsing.
    """
    from docling_core.types.doc import DocItemLabel

    return {
        DocItemLabel.REFERENCE,
        DocItemLabel.PAGE_HEADER,
        DocItemLabel.PAGE_FOOTER,
        DocItemLabel.FOOTNOTE,
    }


_NOISE_LABELS = None  # Lazily populated per-worker.

_converter = None  # Per-worker converter initialized in _init_worker.
_chunker = None
_splitter = None


def _init_worker() -> None:
    """Load Docling models once per worker process.

    Initializing models during worker startup avoids repeating expensive
    model loads for each PDF processed.
    """
    global _converter, _chunker, _splitter

    from docling.chunking import HybridChunker
    from docling.datamodel.accelerator_options import (
        AcceleratorDevice,
        AcceleratorOptions,
    )
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
    from docling.datamodel.settings import settings as docling_settings
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling_core.transforms.chunker.tokenizer.huggingface import (
        HuggingFaceTokenizer,
    )
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from transformers import AutoTokenizer

    pipeline_opts = PdfPipelineOptions()
    pipeline_opts.do_ocr = False  # Academic PDFs are text-based.
    pipeline_opts.do_table_structure = True
    pipeline_opts.table_structure_options.mode = TableFormerMode.ACCURATE
    pipeline_opts.table_structure_options.do_cell_matching = False

    device = _detect_device()
    log.info("Docling using device: %s", device)

    pipeline_opts.accelerator_options = AcceleratorOptions(
        device=AcceleratorDevice.CUDA if device == "cuda" else AcceleratorDevice.CPU,
        cuda_use_flash_attention2=False,
    )

    if device == "cuda":
        docling_settings.perf.page_batch_size = GPU_PAGE_BATCH
    else:
        docling_settings.perf.page_batch_size = 1

    _converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_opts)}
    )
    tokenizer = HuggingFaceTokenizer(
        tokenizer=AutoTokenizer.from_pretrained(EMBEDDING_MODEL_NAME),
        max_tokens=PARENT_MAX_TOKENS,
    )
    _chunker = HybridChunker(tokenizer=tokenizer, merge_peers=True)
    _splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHILD_CHUNK_CHARS,
        chunk_overlap=CHILD_CHUNK_OVERLAP,
    )


# Document and metadata helpers.


def file_paper_id(path: str) -> str:
    """Compute 24-character SHA-256 content hash for identification."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()[:24]


def det_id(*parts: str) -> str:
    """Derive deterministic UUID5 from components for Qdrant point ID."""
    return str(uuid.uuid5(ID_NAMESPACE, ":".join(parts)))


def extract_doi_from_pdf(pdf_path: str) -> str | None:
    """Extract DOI from the initial pages of a PDF via pymupdf.

    Free-text scanning catches DOIs printed in margins or headers
    that document layout parsers do not capture in structured metadata.
    """
    try:
        import pymupdf as _fitz

        doc = _fitz.open(pdf_path)
        for i in range(min(2, len(doc))):
            page_text = doc[i].get_text()
            m = DOI_PATTERN.search(page_text)
            if m:
                doc.close()
                return m.group(2).rstrip(".,;)")
        doc.close()
    except Exception:
        pass
    return None


def extract_doi_from_markdown(text: str) -> str | None:
    """Fallback DOI from the first 3,000 characters of markdown."""
    m = DOI_PATTERN.search(text[:3000])
    if not m:
        return None
    return m.group(2).rstrip(".,;)")


def extract_title(doc, fallback: str) -> str:
    """Extract the paper title from a DoclingDocument.

    Prefers an explicit DocItemLabel.TITLE item when tagged. Otherwise
    falls back to the first SECTION_HEADER preceding body content.
    """
    from docling_core.types.doc import DocItemLabel

    for item, _ in doc.iterate_items():
        if item.label == DocItemLabel.TITLE:
            text = getattr(item, "text", None)
            if text and text.strip():
                return text.strip()

    for item, _ in doc.iterate_items():
        if item.label == DocItemLabel.SECTION_HEADER:
            text = getattr(item, "text", None)
            if text and text.strip():
                return text.strip()

    return fallback


# Stage A: Worker parsing and chunking.


def _normalized_find(haystack: str, needle: str, start: int = 0) -> int:
    """Find needle in haystack after collapsing whitespace runs.

    Normalizes whitespace on both strings while mapping matching offsets
    back to the original un-normalized text for accurate passage spans.
    """
    _ws = re.compile(r"\s+")
    norm_hay = _ws.sub(" ", haystack)
    norm_need = _ws.sub(" ", needle).strip()
    pos = norm_hay.find(norm_need, start)
    if pos == -1:
        return -1
    # Walk original string to map normalised offset back to real offset.
    orig_pos = 0
    norm_pos = 0
    while norm_pos < pos and orig_pos < len(haystack):
        if haystack[orig_pos].isspace():
            while orig_pos < len(haystack) and haystack[orig_pos].isspace():
                orig_pos += 1
            norm_pos += 1
        else:
            orig_pos += 1
            norm_pos += 1
    return orig_pos


def _find_chunk_offset(haystack: str, chunk_text: str, cursor: int = 0) -> int:
    """Find character offset of chunk_text in haystack via anchors.

    Falls back to progressively shorter prefixes to locate text spans
    across minor whitespace and line-break variations introduced by the
    chunker. Returns -1 if no matching anchor is found.
    """
    pos = _normalized_find(haystack, chunk_text, cursor)
    if pos != -1:
        return pos

    pos = _normalized_find(haystack, chunk_text)
    if pos != -1:
        return pos

    for anchor_len in (200, 100, 80, 50):
        if len(chunk_text) <= anchor_len:
            continue
        anchor = chunk_text[:anchor_len]
        pos = _normalized_find(haystack, anchor, cursor)
        if pos != -1:
            return pos
        pos = _normalized_find(haystack, anchor)
        if pos != -1:
            return pos

    period_idx = chunk_text.find(".")
    if period_idx > 20:
        anchor = chunk_text[:period_idx]
        pos = _normalized_find(haystack, anchor, cursor)
        if pos != -1:
            return pos
        pos = _normalized_find(haystack, anchor)
        if pos != -1:
            return pos

    return -1


def fetch_crossref_metadata(title: str, doi: str | None) -> dict | None:
    """Query paper metadata from the Crossref REST API.

    Prefers direct DOI lookup to avoid ambiguous title matches, falling
    back to fuzzy title matching when needed. Retries on HTTP 429 rate
    limits with exponential backoff.
    """
    crossref_url = "https://api.crossref.org/works"
    mailto = "ingest@bloomindex.local"
    timeout = 15.0
    max_retries = 3
    base_delay = 2.0  # seconds

    try:
        import httpx

        with httpx.Client(timeout=timeout) as client:
            # Strategy 1: Direct DOI lookup.
            if doi:
                for attempt in range(max_retries):
                    resp = client.get(
                        f"{crossref_url}/{doi}",
                        params={"mailto": mailto},
                    )
                    if resp.status_code == 429:
                        delay = base_delay * (2**attempt)
                        log.warning("Crossref 429 on DOI lookup, retrying in %.1fs", delay)
                        time.sleep(delay)
                        continue
                    if resp.status_code == 200:
                        data = resp.json()
                        msg = data.get("message", {})
                        d = msg.get("DOI") or doi
                        t = (msg.get("title") or [None])[0]
                        if t:
                            return {
                                "doi": d,
                                "title": t,
                                "authors": [
                                    f"{a.get('given', '')} {a.get('family', '')}".strip()
                                    for a in msg.get("author", [])
                                    if a.get("family")
                                ],
                                "year": (
                                    (
                                        msg.get("published-print")
                                        or msg.get("published-online")
                                        or {}
                                    ).get("date-parts", [[None]])[0][0]
                                ),
                                "journal": (msg.get("container-title") or [None])[0],
                            }
                    break  # Non-retryable status code.

            # Strategy 2: Fuzzy title search.
            if title:
                for attempt in range(max_retries):
                    resp = client.get(
                        crossref_url,
                        params={"query.title": title, "rows": 3, "mailto": mailto},
                    )
                    if resp.status_code == 429:
                        delay = base_delay * (2**attempt)
                        log.warning("Crossref 429 on title search, retrying in %.1fs", delay)
                        time.sleep(delay)
                        continue
                    if resp.status_code == 200:
                        data = resp.json()
                        items = data.get("message", {}).get("items", [])
                        if items:

                            def _score(item):
                                t = (item.get("title") or [None])[0]
                                if not t:
                                    return 0.0
                                return difflib.SequenceMatcher(
                                    None, title.lower(), t.lower()
                                ).ratio()

                            best = max(items, key=_score)
                            score = _score(best)
                            if score >= 0.6:
                                t = (best.get("title") or [None])[0]
                                d = best.get("DOI") or ""
                                if t:
                                    return {
                                        "doi": d,
                                        "title": t,
                                        "authors": [
                                            f"{a.get('given', '')} {a.get('family', '')}".strip()
                                            for a in best.get("author", [])
                                            if a.get("family")
                                        ],
                                        "year": (
                                            (
                                                best.get("published-print")
                                                or best.get("published-online")
                                                or {}
                                            ).get("date-parts", [[None]])[0][0]
                                        ),
                                        "journal": (best.get("container-title") or [None])[0],
                                    }
                    break  # Non-retryable status code.
    except Exception:
        log.warning("Crossref lookup failed for title=%r doi=%r", title, doi, exc_info=True)
    return None


def parse_and_chunk(path: str, paper_id: str) -> dict:
    global _NOISE_LABELS
    if _NOISE_LABELS is None:
        _NOISE_LABELS = _build_noise_labels()

    result = _converter.convert(path)
    doc = result.document
    full_md = doc.export_to_markdown()

    title = extract_title(doc, Path(path).stem)
    doi = extract_doi_from_pdf(path) or extract_doi_from_markdown(full_md)

    cr = fetch_crossref_metadata(title, doi)
    if cr:
        if cr["title"]:
            title = cr["title"]
        if cr["doi"]:
            doi = cr["doi"]

    parent_rows = []
    pending_points = []
    child_idx = 0
    cursor = 0

    for p_idx, chunk in enumerate(_chunker.chunk(dl_doc=doc)):
        headings = chunk.meta.headings or []
        section = headings[-1] if headings else ""

        if chunk.meta.doc_items and all(
            item.label in _NOISE_LABELS for item in chunk.meta.doc_items
        ):
            continue

        page = None
        if chunk.meta.doc_items and chunk.meta.doc_items[0].prov:
            page = chunk.meta.doc_items[0].prov[0].page_no

        start = _find_chunk_offset(full_md, chunk.text, cursor)
        end = (start + len(chunk.text)) if start != -1 else -1
        if start != -1:
            cursor = end

        parent_id = det_id(paper_id, "parent", str(p_idx))
        contextualized = _chunker.contextualize(chunk=chunk)

        parent_rows.append((parent_id, paper_id, contextualized, section, start, end, page))

        for c_text in _splitter.split_text(contextualized):
            pending_points.append(
                {
                    "id": det_id(paper_id, "child", str(child_idx)),
                    "text": c_text,
                    "parent_id": parent_id,
                    "section": section,
                    "page": page,
                }
            )
            child_idx += 1

    return {
        "paper_id": paper_id,
        "path": path,
        "title": title,
        "doi": doi,
        "full_md": full_md,
        "parent_rows": parent_rows,
        "pending_points": pending_points,
    }


# Stage B: Embedding generation, Qdrant upsert, and state persistence.


def embed_and_store(parsed: dict, embeddings, qdrant, conn: sqlite3.Connection) -> int:
    from qdrant_client import models

    paper_id = parsed["paper_id"]
    path = parsed["path"]
    title = parsed["title"]
    doi = parsed["doi"]

    # Cache markdown for citation rendering and passage highlighting.
    Path(MARKDOWN_DIR).mkdir(parents=True, exist_ok=True)
    with open(f"{MARKDOWN_DIR}/{paper_id}.md", "w", encoding="utf-8") as f:
        f.write(parsed["full_md"])

    # Record DOI collisions for review without destructive auto-merging.
    if doi:
        clash = conn.execute(
            "select paper_id, filename from papers where doi = ? and paper_id != ?",
            (doi, paper_id),
        ).fetchone()
        if clash:
            log.warning(
                "DOI collision: %s shares doi=%s with already-indexed %s (%s) "
                "-- possible duplicate. Keep one and run --delete on the other.",
                Path(path).name,
                doi,
                clash[1],
                clash[0],
            )
            conn.execute(
                "insert into doi_collisions (doi, paper_a, paper_b, detected_at) "
                "values (?, ?, ?, ?)",
                (doi, paper_id, clash[0], time.time()),
            )

    pts = parsed["pending_points"]
    if not pts:
        log.warning("%s produced 0 child chunks -- check parsing output.", Path(path).name)

    texts = [p["text"] for p in pts]
    dense_vectors = embeddings.embed_documents(texts) if texts else []

    qdrant_points = [
        models.PointStruct(
            id=p["id"],
            vector={
                DENSE_VEC: dv,
                SPARSE_VEC: models.Document(text=p["text"], model=SPARSE_MODEL),
            },
            payload={
                "page_content": p["text"],
                "metadata": {
                    "paper_id": paper_id,
                    "parent_id": p["parent_id"],
                    "source": Path(path).name,
                    "doc_title": title,
                    "doc_doi": doi,
                    "section_title": p["section"],
                    "page": p["page"],
                    "content_type": "text",
                },
            },
        )
        for p, dv in zip(pts, dense_vectors)
    ]

    if qdrant_points:
        qdrant.upsert(collection_name=COLLECTION_NAME, points=qdrant_points)

    conn.executemany(
        "insert or replace into parents "
        "(parent_id, paper_id, text, section_title, body_start, body_end, page) "
        "values (?,?,?,?,?,?,?)",
        parsed["parent_rows"],
    )
    conn.execute(
        "insert or replace into papers "
        "(paper_id, filename, title, doi, status, num_chunks, error, indexed_at) "
        "values (?,?,?,?,'indexed',?,NULL,?)",
        (paper_id, Path(path).name, title, doi, len(qdrant_points), time.time()),
    )
    conn.commit()
    return len(qdrant_points)


# Qdrant collection management.


def ensure_collection(qdrant) -> None:
    from qdrant_client import models

    if qdrant.collection_exists(COLLECTION_NAME):
        return
    log.info("Creating collection %s...", COLLECTION_NAME)
    qdrant.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config={
            DENSE_VEC: models.VectorParams(
                size=DENSE_DIM,
                distance=models.Distance.COSINE,
            ),
        },
        sparse_vectors_config={
            # IDF modifier is required to enable Qdrant BM25 scoring.
            SPARSE_VEC: models.SparseVectorParams(modifier=models.Modifier.IDF),
        },
        # Disable HNSW graph construction during bulk upserts.
        hnsw_config=models.HnswConfigDiff(m=0),
        optimizers_config=models.OptimizersConfigDiff(indexing_threshold=0),
    )
    qdrant.create_payload_index(
        collection_name=COLLECTION_NAME,
        field_name="metadata.paper_id",
        field_schema="keyword",
    )
    log.info("Collection created.")


def finalize_collection(qdrant) -> None:
    from qdrant_client import models

    log.info("Re-enabling HNSW index build...")
    qdrant.update_collection(
        collection_name=COLLECTION_NAME,
        hnsw_config=models.HnswConfigDiff(m=16),
        optimizers_config=models.OptimizersConfigDiff(indexing_threshold=20000),
    )


# SQLite state store.


def init_db() -> sqlite3.Connection:
    Path(STATE_DB).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(STATE_DB)
    conn.execute("pragma journal_mode=WAL")
    conn.execute("""
        create table if not exists papers (
            paper_id   text primary key,
            filename   text not null,
            title      text,
            doi        text,
            status     text not null default 'pending',
            num_chunks integer,
            error      text,
            indexed_at real
        )
    """)
    conn.execute("""
        create table if not exists parents (
            parent_id     text primary key,
            paper_id      text not null,
            text          text not null,
            section_title text,
            body_start    integer,
            body_end      integer,
            page          integer
        )
    """)
    conn.execute("create index if not exists idx_parents_paper on parents(paper_id)")
    conn.execute("create index if not exists idx_papers_doi on papers(doi)")
    conn.execute("""
        create table if not exists doi_collisions (
            id         integer primary key autoincrement,
            doi        text not null,
            paper_a    text not null,
            paper_b    text not null,
            detected_at real
        )
    """)
    conn.execute("""
        create table if not exists config (
            key   text primary key,
            value text not null
        )
    """)
    conn.commit()
    return conn


# Paper deletion.


def delete_paper(paper_id: str, qdrant, conn: sqlite3.Connection) -> None:
    """Delete a paper and all associated vectors, chunks, and metadata.

    Removes points from Qdrant via payload filter, cleans parent and
    collision rows from SQLite, and deletes the cached markdown file.
    """
    from qdrant_client import models

    log.info("Deleting paper_id=%s from Qdrant...", paper_id)
    qdrant.delete(
        collection_name=COLLECTION_NAME,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="metadata.paper_id",
                        match=models.MatchValue(value=paper_id),
                    ),
                ]
            )
        ),
    )
    conn.execute("delete from parents where paper_id = ?", (paper_id,))
    conn.execute("delete from papers  where paper_id = ?", (paper_id,))
    conn.execute(
        "delete from doi_collisions where paper_a = ? or paper_b = ?",
        (paper_id, paper_id),
    )
    conn.commit()
    md = Path(MARKDOWN_DIR) / f"{paper_id}.md"
    md.unlink(missing_ok=True)
    log.info("Deleted paper_id=%s from Qdrant, SQLite, and markdown.", paper_id)


def get_parent_contexts(parent_ids: list[str], conn: sqlite3.Connection) -> dict:
    if not parent_ids:
        return {}
    placeholders = ",".join("?" * len(parent_ids))
    rows = conn.execute(
        f"select parent_id, text, section_title, body_start, body_end, page "
        f"from parents where parent_id in ({placeholders})",
        parent_ids,
    ).fetchall()
    return {
        r[0]: {
            "text": r[1],
            "section_title": r[2],
            "body_start": r[3],
            "body_end": r[4],
            "page": r[5],
        }
        for r in rows
    }


# CLI command implementations.


def cmd_status(conn: sqlite3.Connection) -> None:
    rows = conn.execute("select status, count(*) from papers group by status").fetchall()
    total = conn.execute("select count(*) from papers").fetchone()[0]
    print(f"\nKnowledge base catalog: {STATE_DB}")
    print(f"  Total papers: {total}")
    for status, n in sorted(rows):
        print(f"  {status:12s}: {n}")

    collisions = conn.execute("select doi, paper_a, paper_b from doi_collisions").fetchall()
    if collisions:
        print(f"\n  DOI collisions: {len(collisions)}")
        for doi, a, b in collisions:
            print(f"    doi={doi}")
            print(f"      {a} <-> {b}")
    print()


def cmd_delete(qdrant, conn: sqlite3.Connection) -> None:
    query = input("Search (filename or title keyword): ").strip()
    if not query:
        print("Cancelled.")
        return

    rows = conn.execute(
        "select paper_id, filename, title, num_chunks from papers "
        "where filename like ? or title like ? "
        "order by filename",
        (f"%{query}%", f"%{query}%"),
    ).fetchall()

    if not rows:
        print(f"No papers found matching '{query}'.")
        return

    print()
    for i, (pid, fname, title, n) in enumerate(rows):
        print(f"  [{i}] {fname}")
        print(f"       title:  {title or '(unknown)'}")
        print(f"       chunks: {n or 0}  |  paper_id: {pid}")
    print()

    if len(rows) == 1:
        idx = 0
    else:
        try:
            idx = int(input(f"Enter number to delete [0-{len(rows) - 1}]: "))
        except ValueError:
            print("Cancelled.")
            return

    if not (0 <= idx < len(rows)):
        print("Invalid selection.")
        return

    pid, fname, title, _ = rows[idx]
    confirm = input(f"\nDelete '{fname}' ({title or 'no title'})? This cannot be undone. [y/N]: ")
    if confirm.strip().lower() != "y":
        print("Cancelled.")
        return

    delete_paper(pid, qdrant, conn)
    print(f"\n'{fname}' has been completely removed from the knowledge base.")


def cmd_ingest(qdrant, conn: sqlite3.Connection, workers: int = PARSE_WORKERS) -> None:
    # Import embeddings lazily to avoid CUDA initialization in workers.
    from backend.src.chat.embeddings import BloomIndexEmbeddings

    embeddings = BloomIndexEmbeddings(model=EMBEDDING_MODEL_NAME)

    ensure_collection(qdrant)

    all_paths = sorted(
        glob.glob(os.path.join(RAW_DIR, "**/*.pdf"), recursive=True)
        + glob.glob(os.path.join(RAW_DIR, "*.pdf"))
    )
    all_paths = sorted(set(all_paths))
    log.info("Found %d PDFs in %s", len(all_paths), RAW_DIR)

    existing = dict(conn.execute("select filename, paper_id from papers").fetchall())

    pending = []
    for path in all_paths:
        pid = file_paper_id(path)
        fname = Path(path).name

        # Content hash change indicates a revised PDF file.
        old_pid = existing.get(fname)
        if old_pid and old_pid != pid:
            log.info("%s: content changed, removing stale entry %s", fname, old_pid)
            delete_paper(old_pid, qdrant, conn)

        row = conn.execute("select status from papers where paper_id = ?", (pid,)).fetchone()
        if row and row[0] == "indexed":
            continue

        pending.append((path, pid))

    log.info(
        "%d to process  |  %d already indexed  |  %d parse workers",
        len(pending),
        len(all_paths) - len(pending),
        workers,
    )

    if not pending:
        log.info("Nothing to do.")
        finalize_collection(qdrant)  # Re-enable HNSW after check.
        return

    ok = failed = 0
    # Use spawn to prevent CUDA driver deadlocks in forked processes.
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(
        max_workers=workers,
        initializer=_init_worker,
        mp_context=ctx,
    ) as pool:
        futures = {pool.submit(parse_and_chunk, path, pid): (path, pid) for path, pid in pending}
        for future in as_completed(futures):
            path, pid = futures[future]
            t0 = time.perf_counter()  # Covers parse, embed, and storage duration.
            try:
                parsed = future.result()
                n_chunks = embed_and_store(parsed, embeddings, qdrant, conn)
                ok += 1
                log.info(
                    "OK   %-50s  %3d chunks  %.1fs",
                    Path(path).name,
                    n_chunks,
                    time.perf_counter() - t0,
                )
            except Exception as exc:
                failed += 1
                conn.execute(
                    "insert or replace into papers "
                    "(paper_id, filename, status, error, indexed_at) "
                    "values (?,?,'error',?,?)",
                    (pid, Path(path).name, str(exc), time.time()),
                )
                conn.commit()
                log.error("FAIL %-50s  %s", Path(path).name, exc)
                traceback.print_exc()

    finalize_collection(qdrant)

    conn.execute(
        "insert or replace into config (key, value) values (?, ?)",
        ("embedding_model", EMBEDDING_MODEL_NAME),
    )
    conn.execute(
        "insert or replace into config (key, value) values (?, ?)",
        ("embedding_dim", str(DENSE_DIM)),
    )
    conn.commit()

    log.info("Done: %d indexed, %d failed. Re-run to retry failures.", ok, failed)


# Entry point.


def main() -> None:
    from qdrant_client import QdrantClient

    parser = argparse.ArgumentParser(description="Knowledge base ingestion pipeline")
    parser.add_argument("--status", action="store_true", help="Print catalog summary and exit")
    parser.add_argument("--delete", action="store_true", help="Interactive paper deletion")
    parser.add_argument(
        "--workers",
        type=int,
        default=PARSE_WORKERS,
        help=f"Parse worker processes (default: {PARSE_WORKERS}). "
        "Raise only with multiple GPUs; competing for one is slower.",
    )
    args = parser.parse_args()

    qdrant = QdrantClient(url=QDRANT_URL)
    conn = init_db()

    if args.status:
        cmd_status(conn)
        sys.exit(0)

    if args.delete:
        cmd_delete(qdrant, conn)
        sys.exit(0)

    cmd_ingest(qdrant, conn, workers=args.workers)


if __name__ == "__main__":
    main()
