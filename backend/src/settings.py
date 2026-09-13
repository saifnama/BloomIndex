"""BloomIndex unified configuration and environment helpers."""

import os
from pathlib import Path

# Load .env files before any module reads os.getenv. Precedence:
#   1. Real OS environment (always wins).
#   2. .env.<BLOOMINDEX_PROFILE> when BLOOMINDEX_PROFILE is set,
#      allowing a single env var to switch the entire app config.
#   3. .env (base shared defaults).
#   4. Defaults declared in this module.
#
# override=False preserves higher-priority values already in env;
# each file only fills in keys that are still unset.
try:
    from dotenv import load_dotenv

    _project_root = Path(__file__).resolve().parent.parent.parent

    _profile = os.environ.get("BLOOMINDEX_PROFILE", "").strip().lower()
    if _profile:
        _profile_file = _project_root / f".env.{_profile}"
        if _profile_file.exists():
            load_dotenv(_profile_file, override=False)

    load_dotenv(_project_root / ".env", override=False)
except ImportError:
    pass  # python-dotenv not installed; rely on system env vars


# Helpers: thin wrappers around os.getenv with safe type casting.


def env(key: str, default: str = "") -> str:
    """Read a string from the environment."""
    return os.getenv(key, default).strip()


def env_int(key: str, default: int = 0) -> int:
    """Read an integer from the environment; return default on error."""
    try:
        val = os.getenv(key, "").strip()
        return int(val) if val else default
    except (TypeError, ValueError):
        return default


def env_float(key: str, default: float = 0.0) -> float:
    """Read a float from the environment; return default on error."""
    try:
        val = os.getenv(key, "").strip()
        return float(val) if val else default
    except (TypeError, ValueError):
        return default


def env_bool(key: str, default: bool = False) -> bool:
    """Read a boolean from the environment (accepts 1/true/yes)."""
    val = os.getenv(key, str(default)).strip().lower()
    return val in {"1", "true", "yes"}


def env_optional(key: str):
    """Return the env value, or None if the key is missing or empty."""
    val = os.getenv(key, "").strip()
    return val if val else None


# RAG Settings

# When QDRANT_URL is set, connect to a Qdrant Server (Docker, cloud,
# or native binary). Server mode supports concurrent clients and
# multiple uvicorn workers, which the embedded client cannot.
# Prefer server mode on Linux/HPC where filesystem flock() may be
# unreliable (Lustre, certain NFS configurations).
QDRANT_URL = env("QDRANT_URL")

# Optional bearer token for Qdrant Cloud or a server started with
# --service.api_key. Ignored in embedded mode.
QDRANT_API_KEY = env("QDRANT_API_KEY")

# Storage path for the embedded Qdrant client. Used only when
# QDRANT_URL is empty. Defaults to <repo>/tmp/qdrant/. Set this
# to a local-disk path on systems with broken flock() support.
# Tilde and relative paths are resolved at startup.
QDRANT_DIR = env("QDRANT_DIR")

RAG_TEMPERATURE = env_float("RAG_TEMPERATURE", 0.1)


def _safe_int(key: str, default: int) -> int:
    """Read an env integer, returning default on any parse error."""
    try:
        return int(os.getenv(key, str(default)))
    except (TypeError, ValueError):
        return default


# LLM_CONTEXT_WINDOW is authoritative; RAG_CONTEXT_WINDOW is a
# deprecated fallback for configs that predate the unified LLM_*
# namespace. LLM_CONTEXT_WINDOW wins when set.
_llm_ctx = _safe_int("LLM_CONTEXT_WINDOW", 0)
LLM_CONTEXT_WINDOW = _llm_ctx or None
RAG_CONTEXT_WINDOW = LLM_CONTEXT_WINDOW or _safe_int("RAG_CONTEXT_WINDOW", 8192)
RAG_EMBEDDING_MODEL = env("RAG_EMBEDDING_MODEL", "BAAI/bge-m3")
_dim = env_optional("RAG_EMBEDDING_DIM")
RAG_EMBEDDING_DIM = int(_dim) if _dim else None
RAG_EMBEDDING_INSTRUCTION = env("RAG_EMBEDDING_INSTRUCTION")
RAG_TOP_K = env_int("RAG_TOP_K", 10)
RAG_SIMILARITY_THRESHOLD = env_float("RAG_SIMILARITY_THRESHOLD", 0.85)
RAG_RERANKER_MODEL = env("RAG_RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
# "fast" mode attributes citations deterministically via a verbatim
# fast-path and a single batched cross-encoder pass, skipping a
# post-stream LLM chunk-selection call. Any other value uses the
# legacy LLM-based selection path.
RAG_CITATION_MODE = env("RAG_CITATION_MODE", "fast")


def _safe_float(key: str, default: float) -> float:
    """Read an env float, returning default on any parse error."""
    try:
        return float(os.getenv(key, str(default)))
    except (TypeError, ValueError):
        return default


# A sentence cites its best chunk when either condition holds:
#   - best score >= FLOOR (absolute certainty threshold), or
#   - best score exceeds the sentence's mean chunk score by >= MARGIN.
# The margin rule is scale-invariant, surviving domain logit shifts
# where a fixed threshold would silently leave everything uncited.
RAG_CITATION_SUPPORT_FLOOR = _safe_float("RAG_CITATION_SUPPORT_FLOOR", 0.0)
RAG_CITATION_SUPPORT_MARGIN = _safe_float("RAG_CITATION_SUPPORT_MARGIN", 1.0)

# Tokens reserved for system prompt, history, and answer. The
# remaining context window is the retrieval budget; sources exceeding
# it are marked omitted_budget and excluded from the LLM context and
# citation pool. Character estimate uses 4 chars per token.
RAG_CONTEXT_RESERVE_TOKENS = int(_safe_float("RAG_CONTEXT_RESERVE_TOKENS", 2000))
RAG_MULTI_GPU = env_bool("RAG_MULTI_GPU", False)
RAG_FLASH_ATTENTION = env_bool("RAG_FLASH_ATTENTION", True)

# NER Settings

# NER_HYBRID=false disables the LLM phase entirely, running
# dictionary-only extraction with zero LLM calls.
NER_HYBRID = env_bool("NER_HYBRID", True)
NER_CONFIDENCE_THRESHOLD = env_float("NER_CONFIDENCE_THRESHOLD", 0.7)
NER_CHUNK_WORDS = env_int("NER_CHUNK_WORDS", 250)
NER_MAX_CHUNKS = env_int("NER_MAX_CHUNKS", 3)
# Larger chunks for PDF uploads reduce LLM call count, since uploads
# run the full dictionary+LLM pipeline over the whole document.
NER_UPLOAD_CHUNK_WORDS = env_int("NER_UPLOAD_CHUNK_WORDS", 600)

# LLM validation retries before falling back to dictionary-only
# entities for a given section.
NER_MAX_ATTEMPTS = env_int("NER_MAX_ATTEMPTS", 2)
# Wall-clock budget for the LLM phase of a full paper extraction.
# Sections still pending when this expires fall back to dictionary
# entities, keeping /paper/json within the frontend request timeout.
# 0 disables the budget (not recommended).
NER_BUDGET_SECONDS = env_float("NER_BUDGET_SECONDS", 240.0)

# LLM Settings
#
# One application-wide connection for RAG, NER, and RAGAS evaluation.
# The target server is configured exclusively via:
#   LLM_API_BASE_URL, LLM_API_KEY, LLM_MODEL.
#
# LLM_API_BASE_URL must be a /v1 API root, not an operation path
# (/v1/chat/completions) or the native Ollama protocol (/api/chat).


LLM_TIMEOUT_SECONDS = _safe_float("LLM_TIMEOUT_SECONDS", 300.0)
LLM_MAX_RETRIES = _safe_int("LLM_MAX_RETRIES", 2)
# LLM_THINKING=false (default) appends LLM_NO_THINK_DIRECTIVE to the
# last user message, a Qwen-template convention that is inert elsewhere.
# LLM_THINKING=true enables chain-of-thought; empty directive appends
# nothing.
#
# LLM_CHAT_TEMPLATE_KWARGS=true sends enable_thinking via SDK
# extra_body. Default off because the official OpenAI endpoint
# rejects unknown top-level fields; enable only for servers that
# tolerate them (OpenRouter, llama.cpp, vLLM).
#
# LLM_API_BASE_URL, LLM_API_KEY, LLM_MODEL, LLM_THINKING,
# LLM_NO_THINK_DIRECTIVE, and LLM_CHAT_TEMPLATE_KWARGS are read live
# inside resolve_llm_settings(), not captured as module-level constants,
# so a BLOOMINDEX_PROFILE switch is never frozen by stale import-time
# snapshots.

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"


class LLMConfigError(ValueError):
    """Raised when the unified LLM connection is missing or malformed."""


def _normalize_llm_base_url(url: str) -> str:
    """Normalize to a /v1 API root.

    Forgives a trailing /v1/chat/completions operation path.
    Rejects the native Ollama /api/chat protocol.
    """
    url = url.strip().rstrip("/")
    if "/api/chat" in url or url.rstrip("/").endswith("/api/tags"):
        raise LLMConfigError(
            "LLM_API_BASE_URL must be an OpenAI-compatible /v1 root, "
            f"not a native Ollama path: {url!r}. "
            "Ollama serves an OpenAI-compatible API at <host>/v1 -- "
            "point LLM_API_BASE_URL there instead."
        )
    if url.endswith("/v1/chat/completions"):
        url = url[: -len("/chat/completions")]
    if not url.rstrip("/").endswith("/v1"):
        raise LLMConfigError(
            "LLM_API_BASE_URL must be a /v1 API root "
            f"(e.g. https://api.openai.com/v1), got: {url!r}."
        )
    return url


class LLMSettings:
    """Immutable application-wide LLM connection."""

    __slots__ = (
        "base_url",
        "api_key",
        "model",
        "timeout",
        "max_retries",
        "thinking",
        "no_think_directive",
        "chat_template_kwargs",
    )

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 300.0,
        max_retries: int = 2,
        thinking: bool = False,
        no_think_directive: str = "/no_think",
        chat_template_kwargs: bool = False,
    ):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.thinking = thinking
        self.no_think_directive = no_think_directive
        self.chat_template_kwargs = chat_template_kwargs

    def __repr__(self) -> str:
        # Exclude api_key to prevent accidental secret leakage in logs.
        return (
            f"LLMSettings(base_url={self.base_url!r}, "
            f"model={self.model!r}, timeout={self.timeout}, "
            f"max_retries={self.max_retries})"
        )


def resolve_llm_settings() -> LLMSettings:
    """Resolve the application-wide LLM connection settings.

    Reads LLM_API_* variables and returns an immutable LLMSettings
    instance. Raises LLMConfigError with an actionable message when
    the configuration is incomplete.
    """
    import os as _os

    has_unified = any(
        _os.getenv(k, "").strip() for k in ("LLM_API_BASE_URL", "LLM_API_KEY", "LLM_MODEL")
    )
    if not has_unified:
        raise LLMConfigError(
            "No LLM configured. Set LLM_API_BASE_URL, LLM_API_KEY, "
            "and LLM_MODEL (see .env.example)."
        )

    base_url = _os.getenv("LLM_API_BASE_URL", "").strip() or DEFAULT_OPENAI_BASE_URL
    base_url = _normalize_llm_base_url(base_url)
    api_key = _os.getenv("LLM_API_KEY", "").strip()
    model = _os.getenv("LLM_MODEL", "").strip()
    if not model:
        raise LLMConfigError(
            "LLM_MODEL is not set. Set LLM_MODEL to a model ID understood by LLM_API_BASE_URL."
        )
    if not api_key:
        if "api.openai.com" in base_url:
            raise LLMConfigError(
                "LLM_API_KEY is not set. Set LLM_API_KEY for the official OpenAI API."
            )
        # Keyless compat servers accept a placeholder value.
        api_key = "not-needed"

    # Read transport and thinking knobs live so a BLOOMINDEX_PROFILE
    # switch is never frozen by stale import-time snapshots.
    return LLMSettings(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=_safe_float("LLM_TIMEOUT_SECONDS", LLM_TIMEOUT_SECONDS),
        max_retries=_safe_int("LLM_MAX_RETRIES", LLM_MAX_RETRIES),
        thinking=_os.getenv("LLM_THINKING", "").strip().lower() in {"1", "true", "yes"},
        no_think_directive=_os.getenv("LLM_NO_THINK_DIRECTIVE", "/no_think"),
        chat_template_kwargs=_os.getenv("LLM_CHAT_TEMPLATE_KWARGS", "").strip().lower()
        in {"1", "true", "yes"},
    )


def llm_status() -> dict:
    """Return a configuration summary for readiness probes."""
    try:
        settings = resolve_llm_settings()
    except LLMConfigError as exc:
        return {"llm": f"unconfigured: {exc}"}
    return {"llm": "configured", "model": settings.model}
