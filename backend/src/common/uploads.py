"""Per-user upload storage and concurrency management.

Organizes temporary runtime assets under isolated user directories:
- chat/{user}/files/: uploaded document corpus.
- chat/{user}/previews/: extracted markdown for citations.
- chat/{user}/jobs/: asynchronous job status records.
- chat/{user}/parents.json: hierarchical chunk parent mapping.

User isolation enables constant-time directory audits and full data
deletion without cross-tenant scans.
"""

import asyncio
import json
import os
import re
import shutil
import tempfile
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from backend.src.common.paths import tmp_dir

_LOCK_ACQUIRE_TIMEOUT = 60.0
_LOCK_IDLE_EVICT_SECONDS = 3600.0


def ensure_dir(path: Path) -> Path:
    """Create directory tree if missing and return the path."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_filename(filename: str) -> str:
    """Sanitize filename to prevent directory traversal and invalid chars."""
    base = os.path.basename(filename or "")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._-")
    return safe or "upload.pdf"


def _safe_user_id(user_id: str) -> str:
    """Sanitize user identifier for safe filesystem path construction."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", user_id or "default")


def chat_home(user_id: str) -> Path:
    """Return root directory for all temporary assets owned by user."""
    return ensure_dir(tmp_dir() / "chat" / _safe_user_id(user_id))


def get_user_upload_dir(user_id: str) -> Path:
    """Return directory holding uploaded documents for the user."""
    return ensure_dir(chat_home(user_id) / "files")


def get_user_upload_file_path(user_id: str, filename: str) -> Path:
    """Resolve sanitized destination path for an uploaded user file."""
    return get_user_upload_dir(user_id) / safe_filename(filename)


def get_user_markdown_file_path(user_id: str, filename: str) -> Path:
    """Return path to extracted markdown sidecar for citation previews.

    Kept in a separate directory so file-globbing over uploaded PDFs
    remains clean.
    """
    return ensure_dir(chat_home(user_id) / "previews") / f"{safe_filename(filename)}.md"


def get_parent_store_path(user_id: str) -> Path:
    """Return path to parent chunk mapping store for the user."""
    return chat_home(user_id) / "parents.json"


def get_user_job_dir(user_id: str) -> Path:
    """Return directory storing asynchronous job status JSON records."""
    return ensure_dir(chat_home(user_id) / "jobs")


def user_jobs(user_id: str) -> "UploadJobStore":
    """Instantiate a job store scoped to the user directory."""
    return UploadJobStore(get_user_job_dir(user_id))


def delete_user_uploads(user_id: str) -> None:
    """Recursively remove user storage tree for cleanup and data wiping."""
    shutil.rmtree(chat_home(user_id), ignore_errors=True)


def delete_user_upload_file(user_id: str, filename: str) -> None:
    """Remove a single uploaded file belonging to the user if present."""
    file_path = get_user_upload_file_path(user_id, filename)
    if file_path.exists():
        file_path.unlink()


def extract_paper_markdown(pdf_path) -> str:
    """Extract markdown from PDF using PyMuPDF text and vector tables.

    Appends extracted tables as GitHub Flavored Markdown and inserts
    page boundary markers (<!-- Page N -->). Both ingestion and preview
    pipelines rely on this shared output to keep chunk byte offsets
    consistent.
    """
    import pymupdf

    text_parts = []
    with pymupdf.open(pdf_path) as doc:
        for idx, page in enumerate(doc):
            parts = [(page.get_text("text") or "").strip()]
            try:
                for table in page.find_tables():
                    rows = table.extract() or []
                    cells = [c for r in rows if r for c in r]
                    # Ignore table artifacts containing no cell text.
                    if not any((c or "").strip() for c in cells):
                        continue
                    md = (table.to_markdown() or "").strip()
                    if md:
                        parts.append(md)
            except Exception:
                # Table parsing is best-effort; preserve extracted text.
                pass
            text = "\n\n".join(p for p in parts if p).strip()
            if text:
                text_parts.append(f"<!-- Page {idx + 1} -->\n\n{text}")
    return "\n\n".join(text_parts)


class UploadJobStore:
    """Persistent job state store scoped to an individual user."""

    def __init__(self, base_dir: Path | str):
        self.base_dir = ensure_dir(Path(base_dir))

    def _job_path(self, job_id: str) -> Path:
        """Return path to JSON record for the specified job ID."""
        return self.base_dir / f"{job_id}.json"

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        """Write JSON payload atomically using temporary file rename."""
        fd, temp_path = tempfile.mkstemp(dir=str(self.base_dir), prefix=path.stem, suffix=".tmp")
        temp_file = Path(temp_path)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
            temp_file.replace(path)
        finally:
            if temp_file.exists():
                temp_file.unlink()

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Record new job status payload to disk."""
        self._write_json(self._job_path(payload["job_id"]), payload)
        return payload

    def get(self, job_id: str) -> dict[str, Any] | None:
        """Load job status payload from disk if present."""
        path = self._job_path(job_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def update(self, job_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        """Update existing job status record with modified fields."""
        existing = self.get(job_id)
        if not existing:
            return None
        existing.update(fields)
        self._write_json(self._job_path(job_id), existing)
        return existing

    def list(self) -> list[dict[str, Any]]:
        """List this user's jobs sorted by creation timestamp."""
        jobs = []
        if not self.base_dir.exists():
            return jobs
        for path in self.base_dir.glob("*.json"):
            try:
                jobs.append(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
        return sorted(jobs, key=lambda item: item.get("created_at", ""))

    def delete(self, job_id: str) -> None:
        """Delete job status record for specified job ID."""
        path = self._job_path(job_id)
        if path.exists():
            path.unlink()

    def clear(self) -> None:
        """Delete all job status records for this user."""
        for path in self.base_dir.glob("*.json"):
            try:
                path.unlink()
            except OSError:
                pass

    def prune(self, max_age_seconds: float = 604800) -> int:
        """Remove job records exceeding the maximum retention age."""
        cutoff = datetime.now(UTC).timestamp() - max_age_seconds
        deleted = 0
        for path in self.base_dir.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                created_at_str = payload.get("created_at")
                if not created_at_str:
                    continue
                created_ts = datetime.fromisoformat(created_at_str).timestamp()
                if created_ts < cutoff:
                    path.unlink()
                    deleted += 1
            except (json.JSONDecodeError, ValueError, OSError):
                continue
        return deleted


class UserLockManager:
    """Manage per-user asyncio mutexes to serialize concurrent tasks."""

    def __init__(
        self,
        acquire_timeout: float = _LOCK_ACQUIRE_TIMEOUT,
        idle_evict_seconds: float = _LOCK_IDLE_EVICT_SECONDS,
    ):
        self._locks: dict[str, tuple[asyncio.Lock, float]] = {}
        self._locks_guard = asyncio.Lock()
        self._acquire_timeout = acquire_timeout
        self._idle_evict_seconds = idle_evict_seconds

    async def _get_lock(self, user_id: str) -> asyncio.Lock:
        """Retrieve existing user lock or allocate a new lock instance."""
        async with self._locks_guard:
            now = time.monotonic()
            # Evict unlocked mutexes that have exceeded the idle TTL.
            for uid, (lock, last_used) in list(self._locks.items()):
                if (
                    uid != user_id
                    and not lock.locked()
                    and now - last_used > self._idle_evict_seconds
                ):
                    del self._locks[uid]
            entry = self._locks.get(user_id)
            if entry is None:
                entry = (asyncio.Lock(), now)
                self._locks[user_id] = entry
            return entry[0]

    def _touch(self, user_id: str) -> None:
        """Update last-used timestamp for an active user lock."""
        entry = self._locks.get(user_id)
        if entry is not None:
            self._locks[user_id] = (entry[0], time.monotonic())

    @asynccontextmanager
    async def lock(self, user_id: str):
        """Acquire user lock with timeout, raising 503 on contention."""
        lock = await self._get_lock(user_id)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=self._acquire_timeout)
        except TimeoutError:
            raise HTTPException(
                status_code=503,
                detail="Another operation for this session is still running. Retry shortly.",
            )
        try:
            yield
        finally:
            lock.release()
            self._touch(user_id)


user_lock_manager = UserLockManager()
