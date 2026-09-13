"""Health and readiness check endpoints."""

import logging

from fastapi import APIRouter, HTTPException

from backend.src.settings import llm_status

router = APIRouter(prefix="/health", tags=["Health"])
logger = logging.getLogger(__name__)


@router.get("/ready")
async def readiness_check():
    """Probe readiness status across core service dependencies.

    Reports overall status as ready, degraded, or down. Inspects:
    - LLM provider configuration and reachability.
    - Vector store connectivity when RAG services are initialized.

    Returns HTTP 503 only when critical datastores (Qdrant) are down.
    """
    health_status = {
        "status": "ready",
        "dependencies": {"llm": "unknown", "qdrant": "deferred"},
    }

    # Verify LLM configuration gate and cached reachability probe.
    try:
        status = llm_status()
        if status["llm"] != "configured":
            health_status["dependencies"]["llm"] = "unconfigured"
            health_status["status"] = "degraded"
        else:
            from backend.src.chat.ai.health import probe_llm

            state, detail = probe_llm()
            health_status["dependencies"]["llm"] = state
            if state != "reachable":
                health_status["status"] = "degraded"
                health_status["dependencies"]["llm_detail"] = detail
    except Exception as e:
        logger.error(f"Health check failed for LLM: {e}")
        health_status["dependencies"]["llm"] = "unreachable"
        health_status["status"] = "degraded"

    # Verify vector store connectivity if RAG service is initialized.
    try:
        from backend.src.chat.service import peek_rag_service

        service = peek_rag_service()
        if service is not None:
            qclient = service._get_qdrant_client()
            qclient.get_collections()
            health_status["dependencies"]["qdrant"] = "up"
        elif health_status["status"] == "ready":
            health_status["status"] = "degraded"
    except Exception as e:
        logger.error(f"Health check failed for Qdrant: {e}")
        health_status["dependencies"]["qdrant"] = "down"
        health_status["status"] = "down"

    if health_status["status"] == "down":
        raise HTTPException(status_code=503, detail=health_status)

    return health_status
