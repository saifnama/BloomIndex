"""NER router for text, DOI, and PDF upload extraction."""

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import uuid4

import pymupdf
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse

from backend.src import settings
from backend.src.common.caching import ner_cache
from backend.src.common.paths import tmp_dir
from backend.src.common.session import (
    attach_session_cookie,
    get_or_set_session_id,
    get_session_id,
)
from backend.src.dependencies import get_ner_service
from backend.src.domain.schemas import Entity, NERRequest, NERResponse
from backend.src.ner.service import NERService, ner_service
from backend.src.papers.europe_pmc import EuropePMCService

router = APIRouter(prefix="/ner", tags=["NER"])
logger = logging.getLogger(__name__)


# Standalone Text Extraction


@router.post("/process")
async def process_text_ner(
    request: dict,
    service: NERService = Depends(get_ner_service),
):
    """Extract entities from raw input text or structured sections."""
    sections = request.get("sections", [])
    if sections:
        summary, entities = await service.process_sections(sections)
    else:
        text = request.get("text", "")
        if not text:
            return {"error": "No text provided", "entities": [], "summary": {}}
        summary, entities = await service.process_text(text, max_chunks=settings.NER_MAX_CHUNKS)

    return {
        "summary": summary,
        "entities": entities,
    }


# --- Legacy JSON Endpoint ---


@router.post("/doi/json", response_model=NERResponse)
async def process_doi_json(request: NERRequest, service: NERService = Depends(get_ner_service)):
    doi = request.doi
    _, clean_id = EuropePMCService.parse_identifier(doi)
    cache_key = f"doi_json::{clean_id}"

    cached = ner_cache.get(cache_key)
    if cached:
        return NERResponse(**{k: v for k, v in cached.items() if not k.startswith("_")})

    text, mode = await EuropePMCService.fetch_paper_data(clean_id)
    if not text:
        raise HTTPException(status_code=404, detail="Paper not found")

    # Strip XML tags so NER extracts against plain text tokens.
    if mode == "full_text":
        text = EuropePMCService.clean_xml(text)

    summary, entities_data = await service.process_text(text, max_chunks=settings.NER_MAX_CHUNKS)
    entities = [Entity(**e) for e in entities_data]

    response = NERResponse(doi=clean_id, mode=mode, text=text[:1000], entities=entities)
    ner_cache.set(cache_key, response.model_dump())
    return response


# Cache Invalidation


@router.delete("/cache/{doi}")
async def clear_ner_cache(doi: str):
    """Evict in-memory and persistent NER caches for a DOI."""
    try:
        _, clean_id = EuropePMCService.parse_identifier(doi)
    except Exception:
        clean_id = doi

    ner_cache.delete(doi)
    ner_cache.delete(clean_id)
    ner_cache.delete(f"doi_json::{clean_id}")
    ner_cache.delete(f"doi_json::{doi}")
    ner_service.result_cache.pop(doi, None)
    ner_service.result_cache.pop(clean_id, None)
    return {"status": "cleared", "doi": doi}


# PDF Upload Pipeline (Temporary storage and signing)
NER_UPLOAD_DIR = os.path.join(str(tmp_dir()), "analyse", "files")
SECRETS_DIR = os.path.join(str(tmp_dir()), "secrets")
PDF_SIGNING_SECRET_FILE = os.path.join(SECRETS_DIR, "analyse_pdf.key")
PDF_URL_TTL_SECONDS = 60 * 60 * 24 * 30


def _build_stored_pdf_name(original_filename: str) -> str:
    stem = Path(original_filename).stem or "paper"
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-") or "paper"
    return f"{safe_stem}_{uuid4().hex}.pdf"


@lru_cache(maxsize=1)
def _get_signing_secret() -> bytes:
    from backend.src.common.secrets import get_or_create_secret

    return get_or_create_secret(PDF_SIGNING_SECRET_FILE, env_var="BLOOMINDEX_PDF_SIGNING_SECRET")


def _build_pdf_token(stored_filename: str, owner_id: str, expires_at: int) -> str:
    payload = f"{stored_filename}:{owner_id}:{expires_at}".encode()
    digest = hmac.new(_get_signing_secret(), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")


def _build_signed_pdf_url(stored_filename: str, owner_id: str) -> str:
    expires_at = int(time.time()) + PDF_URL_TTL_SECONDS
    token = _build_pdf_token(stored_filename, owner_id, expires_at)
    return f"/ner/uploaded/{stored_filename}?expires={expires_at}&token={token}"


def _verify_pdf_token(stored_filename: str, owner_id: str, expires_at: int, token: str) -> bool:
    if expires_at < int(time.time()):
        return False
    expected = _build_pdf_token(stored_filename, owner_id, expires_at)
    return hmac.compare_digest(expected, token)


def _metadata_path(stored_filename: str) -> str:
    return os.path.join(NER_UPLOAD_DIR, f"{stored_filename}.meta.json")


def _write_upload_metadata(stored_filename: str, user_id: str) -> None:
    with open(_metadata_path(stored_filename), "w", encoding="utf-8") as meta_file:
        json.dump({"user_id": user_id}, meta_file)


def _read_upload_metadata(stored_filename: str) -> dict[str, Any]:
    path = _metadata_path(stored_filename)
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as meta_file:
        return json.load(meta_file)


def _cleanup_upload_artifacts(file_path: str, stored_filename: str) -> None:
    for path in (file_path, _metadata_path(stored_filename)):
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                logger.warning("Failed to remove upload artifact: %s", path)


async def extract_metadata_from_pdf(doc: pymupdf.Document) -> dict[str, Any]:
    """Extract metadata from PDF using PyMuPDF."""
    meta = doc.metadata
    title = meta.get("title", "")
    author = meta.get("author", "")
    subject = meta.get("subject", "")
    creator = meta.get("creator", "")
    producer = meta.get("producer", "")

    doi_pattern = r"10\.\d{4,}/[^\s]+"
    doi = ""

    search_text = f"{title} {author} {subject} {creator} {producer}"
    doi_match = re.search(doi_pattern, search_text)
    if doi_match:
        doi = doi_match.group()

    if not doi and doc.page_count > 0:
        page1_text = doc[0].get_text("text", sort=True)
        doi_match = re.search(doi_pattern, page1_text)
        if doi_match:
            doi = doi_match.group()[:100]

    return {
        "title": title or "Untitled",
        "doi": doi,
    }


async def extract_text_from_pdf(doc: pymupdf.Document) -> str:
    """Extract full text from PDF using PyMuPDF."""
    text_parts = []

    for page_num in range(doc.page_count):
        page = doc[page_num]
        text = page.get_text()
        if text.strip():
            text_parts.append(text)

    return "\n\n".join(text_parts)


async def extract_entities_full(
    text: str,
) -> tuple[
    dict[str, list[str]],
    dict[str, dict[str, int]],
    dict[str, dict[str, dict[str, Any]]],
]:
    """Execute hybrid NER pipeline across text chunks.

    Splits input text into word chunks, runs dictionary matchers and
    configured LLM extractors under a time budget, and merges canonical
    names with occurrence counts.
    """
    from backend.src.ner.service import ner_service
    from backend.src.settings import NER_UPLOAD_CHUNK_WORDS

    text = (text or "").strip()
    if not text:
        return {}, {}, {}

    chunks = ner_service.split_into_word_chunks(text, NER_UPLOAD_CHUNK_WORDS)
    sections = [{"title": f"Part {i + 1}", "content": chunk} for i, chunk in enumerate(chunks)]
    summary, filtered = await ner_service.process_sections(sections)

    # Map lowercased variant to canonical representation per label.
    variant_canonical: dict[tuple, str] = {}
    canonical_aliases: dict[tuple, set] = {}
    for e in filtered:
        label = e.get("label", "")
        variant = (e.get("text") or "").strip()
        if not label or not variant:
            continue
        canon = (e.get("canonical") or variant).strip()
        variant_canonical[(label, variant.lower())] = canon
        key = (label, canon.lower())
        aliases = canonical_aliases.setdefault(key, set())
        aliases.add(variant)
        for alias in e.get("aliases") or []:
            if alias and str(alias).strip():
                aliases.add(str(alias).strip())

    # Aggregate occurrence counts grouped by canonical entity text.
    merged: dict[str, dict[str, dict[str, Any]]] = {}
    for label, items in summary.items():
        for item in items:
            display = (item.get("text") or "").strip()
            count = int(item.get("count") or 0)
            if not display or count <= 0:
                continue
            canon = variant_canonical.get((label, display.lower()), display)
            buckets = merged.setdefault(label, {})
            bucket = buckets.setdefault(
                canon.lower(), {"canonical": canon, "count": 0, "aliases": set()}
            )
            bucket["count"] += count
            bucket["aliases"].add(display)
            bucket["aliases"].update(canonical_aliases.get((label, canon.lower()), set()))
            bucket["aliases"].discard(bucket["canonical"])

    entities: dict[str, list[str]] = {}
    count_map: dict[str, dict[str, int]] = {}
    canonical_data: dict[str, dict[str, dict[str, Any]]] = {}
    for out_label, buckets in merged.items():
        entities[out_label] = []
        count_map[out_label] = {}
        canonical_data[out_label] = {}
        for canon_lower, bucket in sorted(buckets.items(), key=lambda kv: -kv[1]["count"]):
            entities[out_label].append(bucket["canonical"])
            count_map[out_label][canon_lower] = bucket["count"]
            canonical_data[out_label][canon_lower] = {
                "canonical": bucket["canonical"],
                "display_text": bucket["canonical"],
                "aliases": sorted(bucket["aliases"]),
            }
    return entities, count_map, canonical_data


@router.post("/upload/json")
async def upload_pdf_for_ner(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Upload PDF for text parsing and entity extraction.

    Enforces size limits (50 MB), magic bytes (%PDF-), and page caps
    (500 pages) before extracting metadata, text, and entities.
    """
    _MAX_PDF_BYTES = 50 * 1024 * 1024  # 50 MB
    _MAX_PDF_PAGES = 500

    original_filename = file.filename or "paper.pdf"
    if not original_filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files accepted")

    # Reject upfront if client declared Content-Length exceeds limit.
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > _MAX_PDF_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"PDF exceeds {_MAX_PDF_BYTES // (1024 * 1024)} MB limit.",
                )
        except ValueError:
            pass  # Malformed header will be caught by read buffer cap.

    os.makedirs(NER_UPLOAD_DIR, exist_ok=True)

    user_id = get_or_set_session_id(request, response)
    stored_filename = _build_stored_pdf_name(original_filename)
    file_path = os.path.join(NER_UPLOAD_DIR, stored_filename)

    try:
        content = await file.read(_MAX_PDF_BYTES + 1)
        if len(content) > _MAX_PDF_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"PDF exceeds {_MAX_PDF_BYTES // (1024 * 1024)} MB limit.",
            )
        # Validate magic bytes to verify PDF format integrity.
        if not content[:5] == b"%PDF-":
            raise HTTPException(
                status_code=415,
                detail="File does not appear to be a valid PDF (bad magic bytes).",
            )
        with open(file_path, "wb") as output_file:
            output_file.write(content)
        _write_upload_metadata(stored_filename, user_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to save PDF: %s", exc)
        _cleanup_upload_artifacts(file_path, stored_filename)
        raise HTTPException(status_code=500, detail="Failed to save file") from exc

    doc: pymupdf.Document | None = None
    try:
        doc = pymupdf.open(file_path)
        if doc.page_count > _MAX_PDF_PAGES:
            raise HTTPException(
                status_code=413,
                detail=f"PDF has {doc.page_count} pages; maximum allowed is {_MAX_PDF_PAGES}.",
            )
        metadata = await extract_metadata_from_pdf(doc)
        text = await extract_text_from_pdf(doc)
        entities_by_type, entity_counts, canonical_data = await extract_entities_full(text)

        total_entities = sum(sum(counts.values()) for counts in entity_counts.values())
        entities_with_counts: dict[str, list[dict[str, Any]]] = {}
        for label, texts in entities_by_type.items():
            entities_with_counts[label] = []
            for txt in texts:
                txt_lower = txt.lower()
                count = entity_counts.get(label, {}).get(txt_lower, 1)
                entry: dict[str, Any] = {"text": txt, "count": count}
                meta = canonical_data.get(label, {}).get(txt_lower)
                if meta:
                    entry["canonical"] = meta["canonical"]
                    entry["aliases"] = meta["aliases"]
                entities_with_counts[label].append(entry)

        return {
            "filename": original_filename,
            "stored_filename": stored_filename,
            "pdf_url": _build_signed_pdf_url(stored_filename, user_id),
            "metadata": {
                "title": metadata.get("title", "Untitled"),
                "doi": metadata.get("doi", ""),
            },
            "entity_count": total_entities,
            "entities": entities_by_type,
            "entity_counts": entities_with_counts,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("PDF NER failed: %s", exc)
        _cleanup_upload_artifacts(file_path, stored_filename)
        raise HTTPException(status_code=500, detail=f"Failed to process PDF: {str(exc)}") from exc
    finally:
        if doc is not None:
            doc.close()


@router.get("/uploaded/{stored_filename}")
async def view_uploaded_pdf(
    request: Request,
    response: Response,
    stored_filename: str,
    expires: int = Query(...),
    token: str = Query(...),
) -> FileResponse:
    safe_name = os.path.basename(stored_filename)
    if safe_name != stored_filename or not safe_name.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Invalid PDF path")

    file_path = os.path.join(NER_UPLOAD_DIR, safe_name)
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="PDF not found")

    metadata = _read_upload_metadata(safe_name)
    owner_id = metadata.get("user_id", "default")
    session_id = get_session_id(request)
    if session_id != owner_id or not _verify_pdf_token(safe_name, owner_id, expires, token):
        raise HTTPException(status_code=403, detail="PDF access denied")

    file_response = FileResponse(file_path, media_type="application/pdf")
    attach_session_cookie(file_response, request, session_id)
    return file_response


@router.delete("/uploaded/{stored_filename}")
async def delete_uploaded_pdf(
    request: Request,
    response: Response,
    stored_filename: str,
) -> dict[str, str]:
    """Delete an uploaded PDF and its metadata."""
    safe_name = os.path.basename(stored_filename)
    if safe_name != stored_filename or not safe_name.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Invalid PDF path")

    file_path = os.path.join(NER_UPLOAD_DIR, safe_name)
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="PDF not found")

    metadata = _read_upload_metadata(safe_name)
    owner_id = metadata.get("user_id", "default")
    session_id = get_session_id(request)
    if session_id != owner_id:
        raise HTTPException(status_code=403, detail="PDF access denied")

    _cleanup_upload_artifacts(file_path, safe_name)
    return {"status": "success", "message": "PDF deleted"}
