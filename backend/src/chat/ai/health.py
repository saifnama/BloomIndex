"""Probe remote LLM endpoint availability and credentials.

Queries the non-metered metadata endpoint rather than invoking token
generation to verify network reachability and authentication without
incurring usage costs. Results are cached briefly to avoid redundant
network overhead during high-frequency health checks.
"""

import logging
import time

logger = logging.getLogger(__name__)

_CACHE_TTL = 60.0
_last_at = 0.0
_last_result: tuple[str, str] = ("unknown", "no probe yet")


def probe_llm() -> tuple[str, str]:
    """Check connectivity against the configured language model service.

    Returns a status category ('reachable', 'unreachable', or
    'unconfigured') alongside diagnostic details or model identifiers.
    """
    global _last_at, _last_result
    now = time.monotonic()
    if now - _last_at < _CACHE_TTL:
        return _last_result
    try:
        from backend.src.settings import resolve_llm_settings
    except Exception as exc:
        _last_result = ("unconfigured", f"settings error: {exc}")
        _last_at = now
        return _last_result
    try:
        settings = resolve_llm_settings()
    except Exception as exc:
        _last_result = ("unconfigured", str(exc))
        _last_at = now
        return _last_result
    try:
        import httpx

        resp = httpx.get(
            settings.base_url.rstrip("/") + "/models",
            headers={"Authorization": f"Bearer {settings.api_key}"},
            timeout=5.0,
        )
        if resp.status_code == 200:
            _last_result = ("reachable", settings.model)
        elif resp.status_code in (401, 403):
            _last_result = ("unreachable", "auth rejected (401/403)")
        else:
            _last_result = ("unreachable", f"HTTP {resp.status_code}")
    except Exception as exc:
        logger.warning(f"LLM probe failed: {exc}")
        _last_result = ("unreachable", f"{type(exc).__name__}: {exc}")
    _last_at = now
    return _last_result
