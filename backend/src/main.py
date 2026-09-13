# BloomIndex backend entrypoint.
#
# Configure threading and worker pool defaults before importing ML
# libraries (fastembed, transformers, torch, numpy via MKL/OpenMP).
# Restricting pool sizes prior to import prevents worker-fork crashes
# and resource contention with the main event loop.
#
# setdefault ensures explicit environment variables take precedence.
#
# Knobs:
#   JOBLIB_MULTIPROCESSING: '0' forces joblib execution in main thread.
#   LOKY_MAX_CPU_COUNT: Limits fastembed BM25 worker processes to 1.
#   TOKENIZERS_PARALLELISM: 'false' avoids fork-parallelism crashes.
#   OMP_NUM_THREADS: Caps OpenMP threads to half CPU count for headroom.
#   MKL_NUM_THREADS: Matches Intel MKL thread count to OpenMP cap.
import os as _os

_os.environ.setdefault("JOBLIB_MULTIPROCESSING", "0")
_os.environ.setdefault("LOKY_MAX_CPU_COUNT", "1")
_os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
_cpu_count = _os.cpu_count() or 1
_os.environ.setdefault("OMP_NUM_THREADS", str(max(1, _cpu_count // 2)))
_os.environ.setdefault("MKL_NUM_THREADS", str(max(1, _cpu_count // 2)))
del _cpu_count

# Spawning fresh child processes avoids inheriting invalid interpreter
# state or file descriptors during shutdown.
import multiprocessing as _mp

try:
    _mp.set_start_method("spawn", force=True)
except RuntimeError:
    pass
del _mp

# Enable native stack traces on fatal segmentation faults.
import faulthandler as _fh

_fh.enable()
del _fh


import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.src.common.http_client import HttpClientManager
from backend.src.common.paths import frontend_dist as _frontend_dist_path
from backend.src.common.paths import safe_join
from backend.src.exceptions import NotFoundError, install_handlers
from backend.src.middleware import RequestIDMiddleware
from backend.src.routers import dashboard, doi, health, ner, paper, rag, search

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm shared HTTP client pool before serving incoming requests.
    await HttpClientManager.get_client()
    # Preload NER dictionaries off the event loop to prevent first-query
    # latency spikes. Failures fall back to per-request lazy loading.
    try:
        import asyncio as _asyncio
        import time as _time

        from backend.src.ner.dictionary import preload_dictionaries

        _t0 = _time.perf_counter()
        _n = await _asyncio.to_thread(preload_dictionaries)
        logger.info(f"Preloaded {_n} NER dictionaries in {_time.perf_counter() - _t0:.1f}s.")
    except Exception as exc:
        logger.warning(
            f"Dictionary preload skipped ({exc}); matchers will load lazily on first request."
        )
    logger.info("BloomIndex backend startup complete.")
    yield
    # Drain in-flight HTTP requests before closing service resources.
    await HttpClientManager.close_client()
    try:
        from backend.src.common.llm_client import close_llm_client

        await close_llm_client()
    except Exception as exc:
        logger.warning(f"LLM client shutdown raised (ignored): {exc}")
    try:
        # Explicit closure before interpreter shutdown prevents
        # destructor warnings caused by missing module state.
        from backend.src.chat.service import peek_rag_service

        svc = peek_rag_service()
        if svc is not None:
            svc.close()
    except Exception as exc:
        logger.warning(f"RAG service shutdown raised (ignored): {exc}")


app = FastAPI(
    title="BloomIndex Backend",
    description="Production-ready FastAPI backend for NER and RAG on research papers.",
    version="2.0.0",
    lifespan=lifespan,
)


frontend_origins = [
    origin.strip()
    for origin in os.getenv(
        "BLOOMINDEX_FRONTEND_ORIGINS",
        "http://localhost:8000,http://127.0.0.1:8000,http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]

# Mount static assets when a production frontend build is present.
frontend_dist = os.fspath(_frontend_dist_path())
if os.path.exists(frontend_dist):
    app.mount(
        "/assets",
        StaticFiles(directory=os.path.join(frontend_dist, "assets")),
        name="frontend-assets",
    )

# Allow configured frontend origins for browser clients.
app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

install_handlers(app)
app.add_middleware(RequestIDMiddleware)
app.include_router(search.router)
app.include_router(paper.router)
app.include_router(ner.router)
app.include_router(rag.router)
app.include_router(health.router)
app.include_router(doi.router)
app.include_router(dashboard.router)


API_PREFIXES = (
    "/search",
    "/paper",
    "/ner",
    "/health",
    "/doi",
    "/api",
    "/static",
    "/assets",
)


@app.get("/{full_path:path}")
async def serve_spa(request: Request, full_path: str):
    """Serve the React SPA for all non-API routes."""
    # Unmatched API routes return 404 instead of falling back to SPA.
    if any(full_path.startswith(prefix.lstrip("/")) for prefix in API_PREFIXES):
        raise NotFoundError("Not found", code="unknown_api_path")

    dist = _frontend_dist_path()
    index_path = dist / "index.html"

    # Serve static files directly if present in the build directory.
    resolved = safe_join(dist, full_path) if full_path else None
    if resolved is not None and resolved.is_file():
        return FileResponse(os.fspath(resolved))

    # Fall back to index.html for client-side route handling.
    if index_path.exists():
        return FileResponse(os.fspath(index_path))

    raise NotFoundError(
        "Frontend not built. Run 'cd frontend && bun run build' first.",
        code="frontend_missing",
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="localhost", port=8000)
