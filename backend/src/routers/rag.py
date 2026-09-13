"""RAG chat and document ingestion router."""

import asyncio
import json
import logging
import os
import re
import shutil
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from backend.src.common.session import attach_session_cookie, get_or_set_session_id
from backend.src.common.uploads import (
    extract_paper_markdown,
    get_user_markdown_file_path,
    get_user_upload_file_path,
    user_jobs,
    user_lock_manager,
)
from backend.src.dependencies import get_rag_service
from backend.src.domain.schemas import (
    ChatMessage,
    IndexedFileInfo,
    QueryRequest,
    QueryResponse,
    UploadJobStatus,
    UploadResponse,
)

router = APIRouter(prefix="/api/chat", tags=["Chat"])
logger = logging.getLogger(__name__)

# Match markdown References headings appended by backend or model.
_REF_HEADING_RE = re.compile(r"(?m)^[ \t]*(?:#{1,6}[ \t]+References|\*\*References\*\*)[ \t]*$")


def _collapse_duplicate_references(text: str) -> str:
    """Retain only the authoritative terminal References section.

    When the model generates an intermediate references block and the
    backend appends citations, discard earlier blocks and keep the last.
    """
    if not text or "**References**" not in text and "References" not in text:
        return text
    parts = _REF_HEADING_RE.split(text)
    if len(parts) <= 2:
        return text
    head = parts[0].rstrip()
    head = re.sub(r"\n[ \t]*---[ \t]*$", "", head).rstrip()
    sep = "\n\n---\n\n**References**\n" if head else "**References**\n"
    return head + sep + parts[-1].lstrip()


async def _process_upload_job(job_id: str, saved_paths: list[str], parser_type: str, user_id: str):
    """Process and index uploaded documents in a background task."""
    service = get_rag_service()
    job_store = user_jobs(user_id)
    try:
        async with user_lock_manager.lock(user_id):
            # Offload CPU-bound embedding generation to worker thread.
            indexed_files, _ = await asyncio.to_thread(
                service.process_and_index_pdfs_with_texts,
                saved_paths,
                parser_type=parser_type,
                user_id=user_id,
            )

            job_store.update(
                job_id,
                {
                    "status": "completed",
                    "message": f"Successfully indexed {len(indexed_files)} files using {parser_type}",
                    "files": indexed_files,
                    "summaries": None,
                    "completed_at": datetime.now(UTC).isoformat(),
                },
            )
        logger.info(f"Upload job {job_id} completed: {len(indexed_files)} files indexed")
    except Exception as e:
        logger.exception(f"Upload job {job_id} failed: {e}")
        job_store.update(
            job_id,
            {
                "status": "failed",
                "message": f"Indexing failed: {str(e)}",
                "error": str(e),
                "completed_at": datetime.now(UTC).isoformat(),
            },
        )


@router.post("/upload/json", response_model=UploadResponse)
async def upload_pdfs_json(
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    service: Any = Depends(get_rag_service),
    parser_type: str | None = Form("pymupdf"),
):
    """Queue uploaded PDF documents for background parsing and indexing.

    Accepts parser_type 'pymupdf' (fast) or 'docling' (detailed).
    """
    user_id = get_or_set_session_id(request, response)

    # Validate parser_type
    if parser_type not in ("pymupdf", "docling"):
        parser_type = "pymupdf"

    saved_paths = []
    async with user_lock_manager.lock(user_id):
        for file in files:
            if not file.filename.lower().endswith(".pdf"):
                continue

            file_path = get_user_upload_file_path(user_id, file.filename)
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            saved_paths.append(str(file_path))

    if not saved_paths:
        raise HTTPException(status_code=400, detail="No valid PDF files uploaded")

    job_id = str(uuid.uuid4())
    filenames = [os.path.basename(p) for p in saved_paths]
    user_jobs(user_id).create(
        {
            "job_id": job_id,
            "user_id": user_id,
            "status": "processing",
            "message": f"Processing {len(saved_paths)} file(s) with {parser_type}...",
            "files": filenames,
            "parser_type": parser_type,
            "summaries": None,
            "error": None,
            "created_at": datetime.now(UTC).isoformat(),
            "completed_at": None,
        }
    )

    background_tasks.add_task(_process_upload_job, job_id, saved_paths, parser_type, user_id)
    logger.info(f"Upload job {job_id} queued for user {user_id}: {len(saved_paths)} file(s)")

    return UploadResponse(
        status="processing",
        message=f"Processing {len(saved_paths)} file(s) with {parser_type}. Poll /api/chat/upload/status/{job_id} for updates.",
        files=filenames,
        summaries=None,
        job_id=job_id,
    )


@router.get("/upload/status/{job_id}", response_model=UploadJobStatus)
async def get_upload_status(request: Request, response: Response, job_id: str):
    """Get the status of an async upload job."""
    user_id = get_or_set_session_id(request, response)
    job = user_jobs(user_id).get(job_id)
    if not job or job.get("user_id") != user_id:
        raise HTTPException(status_code=404, detail="Job not found")
    return UploadJobStatus(**job)


@router.get("/upload/jobs")
async def list_upload_jobs(request: Request, response: Response):
    """List active upload jobs for the current user only."""
    user_id = get_or_set_session_id(request, response)
    return user_jobs(user_id).list()


@router.get("/files/json", response_model=list[IndexedFileInfo])
async def list_indexed_files(
    request: Request,
    response: Response,
    service: Any = Depends(get_rag_service),
):
    """List documents indexed in the vector store for the user."""
    user_id = get_or_set_session_id(request, response)
    return service.list_indexed_files(user_id)


@router.get("/files/{filename}/content")
async def get_uploaded_file_content(
    request: Request,
    response: Response,
    filename: str,
):
    """Return the uploaded PDF file for inline viewing for the current user."""
    user_id = get_or_set_session_id(request, response)
    safe_filename = os.path.basename(filename)
    if safe_filename != filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    file_path = get_user_upload_file_path(user_id, safe_filename)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    file_response = FileResponse(
        str(file_path),
        media_type="application/pdf",
        filename=safe_filename,
        content_disposition_type="inline",
    )
    attach_session_cookie(file_response, request, user_id)
    return file_response


@router.get("/files/{filename}/markdown")
async def get_uploaded_file_markdown(
    request: Request,
    response: Response,
    filename: str,
):
    """Return the extracted markdown view of an uploaded paper.

    Citation previews highlight chunks against this text. If markdown
    was not generated during ingestion, extracts it on demand and
    caches the result.
    """
    user_id = get_or_set_session_id(request, response)
    safe_filename = os.path.basename(filename)
    if safe_filename != filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    md_path = get_user_markdown_file_path(user_id, safe_filename)
    pdf_path = get_user_upload_file_path(user_id, safe_filename)

    # Lazy extraction in worker thread for legacy uploads.
    # Shared extractor maintains exact character offset alignment.
    if not md_path.is_file():
        if not pdf_path.is_file():
            raise HTTPException(status_code=404, detail="File not found")

        try:
            full_text = await asyncio.to_thread(extract_paper_markdown, str(pdf_path))
        except Exception as e:
            logger.warning(f"Lazy markdown regen failed for {safe_filename}: {e}")
            raise HTTPException(status_code=500, detail="Failed to extract markdown.")

        try:
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(full_text, encoding="utf-8")
        except Exception as e:
            # Failure to cache is non-fatal for returning the payload.
            logger.warning(f"Failed to cache markdown for {safe_filename}: {e}")

        attach_session_cookie(response, request, user_id)
        # Avoid caching stale offsets if the file is re-uploaded.
        response.headers["Cache-Control"] = "no-store, must-revalidate"
        return {"markdown": full_text}

    try:
        markdown_text = md_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"Failed to read cached markdown for {safe_filename}: {e}")
        raise HTTPException(status_code=500, detail="Failed to read markdown.")

    attach_session_cookie(response, request, user_id)
    # Avoid caching stale offsets across client sessions.
    response.headers["Cache-Control"] = "no-store, must-revalidate"
    return {"markdown": markdown_text}


@router.delete("/files/{filename}")
async def delete_source(
    request: Request,
    response: Response,
    filename: str,
    service: Any = Depends(get_rag_service),
):
    """Remove a source completely: delete chunks from vector store."""
    user_id = get_or_set_session_id(request, response)
    async with user_lock_manager.lock(user_id):
        success = service.delete_source(filename, user_id)
    if not success:
        raise HTTPException(status_code=500, detail=f"Failed to delete '{filename}'")
    return {
        "status": "success",
        "message": f"Deleted '{filename}' and all its embeddings.",
    }


@router.post("/reset")
async def reset_rag_data(
    request: Request,
    response: Response,
    service: Any = Depends(get_rag_service),
):
    """Permanently delete all indexed chunks for the user."""
    user_id = get_or_set_session_id(request, response)
    async with user_lock_manager.lock(user_id):
        success = service.reset_rag(user_id)
        if success:
            user_jobs(user_id).clear()
    if not success:
        raise HTTPException(status_code=500, detail="Failed to reset RAG data.")
    return {
        "status": "success",
        "message": "All chat history and sources permanently deleted for user.",
    }


@router.post("/cleanup")
async def cleanup_user_data(
    request: Request,
    response: Response,
    service: Any = Depends(get_rag_service),
):
    """Clean up all data for a user when they close their browser.

    Deletes vector store collections, uploads, and all user files.
    """
    user_id = get_or_set_session_id(request, response)
    async with user_lock_manager.lock(user_id):
        success = service.cleanup_user(user_id)
        if success:
            user_jobs(user_id).clear()
    if not success:
        raise HTTPException(status_code=500, detail="Failed to cleanup user data.")
    return {"status": "success", "message": "All user data cleaned up."}


@router.post("/query/json", response_model=QueryResponse)
async def query_rag_json(
    http_request: Request,
    response: Response,
    payload: QueryRequest,
    service: Any = Depends(get_rag_service),
):
    user_id = get_or_set_session_id(http_request, response)
    try:
        # Convert chat_history from Pydantic models to dicts
        history = None
        if payload.chat_history:
            history = [{"role": m.role, "content": m.content} for m in payload.chat_history]

        result = await service.query(
            payload.query,
            filter_files=payload.selected_files,
            user_id=user_id,
            chat_history=history,
        )
        if result.get("answer"):
            result["answer"] = _collapse_duplicate_references(result["answer"])
        return QueryResponse(**result)
    except Exception as e:
        from backend.src.chat.llm import RAGLLMTimeoutError, RAGProviderAuthError

        if isinstance(e, RAGProviderAuthError):
            raise HTTPException(status_code=502, detail=str(e))
        if isinstance(e, RAGLLMTimeoutError):
            raise HTTPException(status_code=504, detail=str(e))
        raise HTTPException(status_code=500, detail=f"Query failed: {str(e)}")


@router.post("/query/stream")
async def query_rag_stream(
    http_request: Request,
    response: Response,
    payload: QueryRequest,
    service: Any = Depends(get_rag_service),
):
    """Stream RAG response tokens and citations as NDJSON frames.

    Yields newline-delimited JSON objects containing text deltas,
    citation references, error notifications, or completion markers.
    """
    user_id = get_or_set_session_id(http_request, response)
    history = None
    if payload.chat_history:
        history = [{"role": m.role, "content": m.content} for m in payload.chat_history]

    async def frame_generator():
        try:
            async for frame in service.query_stream(
                payload.query,
                filter_files=payload.selected_files,
                user_id=user_id,
                chat_history=history,
            ):
                # Ensure terminal frame has deduplicated references.
                if frame.get("type") == "answer_corrected" and frame.get("text"):
                    frame["text"] = _collapse_duplicate_references(frame["text"])
                yield json.dumps(frame) + "\n"
        except Exception as e:
            logger.exception("query_stream endpoint failed")
            yield json.dumps({"type": "error", "error": str(e)}) + "\n"

    # Disable proxy response buffering to stream tokens to client.
    return StreamingResponse(
        frame_generator(),
        media_type="application/x-ndjson",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
        },
    )


class SuggestRequest(BaseModel):
    """Payload containing recent conversation messages for suggestions."""

    messages: list[ChatMessage]


class SuggestionItem(BaseModel):
    prompt: str


class SuggestResponse(BaseModel):
    suggestions: list[SuggestionItem]


@router.post("/suggest", response_model=SuggestResponse)
async def suggest_followups(
    http_request: Request,
    response: Response,
    payload: SuggestRequest,
    service: Any = Depends(get_rag_service),
):
    """Generate follow-up question suggestions based on conversation history.

    Returns HTTP 200 with an empty list on generation failure.
    """
    get_or_set_session_id(http_request, response)
    history = [{"role": m.role, "content": m.content} for m in payload.messages]
    try:
        prompts = await service.suggest_followups(history)
    except Exception as e:
        logger.warning(f"suggest_followups failed: {e}")
        prompts = []
    return SuggestResponse(suggestions=[SuggestionItem(prompt=p) for p in prompts])
