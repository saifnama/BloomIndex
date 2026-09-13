"""Embedding and accelerator setup for RAG ingestion and retrieval.

Manages device selection (CUDA, MPS, CPU), flash attention, Matryoshka
Representation Learning (MRL) dimension truncation, and lazy model
lifecycle loading.
"""

import logging
import os
import threading
import time
from typing import Any

from backend.src.settings import (
    QDRANT_DIR,
    RAG_EMBEDDING_DIM,
    RAG_EMBEDDING_INSTRUCTION,
    RAG_EMBEDDING_MODEL,
    RAG_FLASH_ATTENTION,
    RAG_MULTI_GPU,
    RAG_RERANKER_MODEL,
    RAG_SIMILARITY_THRESHOLD,
    RAG_TOP_K,
)

logger = logging.getLogger(__name__)

from langchain_core.embeddings import Embeddings as _LCEmbeddings

from backend.src.chat.config import (
    RAG_RERANK_BATCH_SIZE,
    RAG_RERANK_CANDIDATE_K,
)
from backend.src.common.paths import tmp_dir

# Suppress transformers tokenizer sequence length warnings globally
os.environ["TRANSFORMERS_VERBOSITY"] = "error"


def get_optimal_device() -> str:
    """Detect the best available accelerator: cuda > mps > cpu.

    Can be overridden via the ``RAG_DEVICE`` environment variable.
    """
    env_override = os.getenv("RAG_DEVICE", "").strip().lower()
    if env_override:
        return env_override

    try:
        import torch
    except ImportError:
        return "cpu"

    if torch.cuda.is_available() and torch.cuda.device_count() > 0:
        return "cuda"

    # Apple Silicon MPS
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"

    return "cpu"


def _has_multiple_gpus() -> bool:
    """Return True when CUDA is available and more than one GPU is visible."""
    try:
        import torch

        return torch.cuda.is_available() and torch.cuda.device_count() > 1
    except ImportError:
        return False


def _flash_attn_available() -> bool:
    """Check whether flash-attn is installed and importable."""
    try:
        import flash_attn

        return True
    except ImportError:
        return False


def _build_cuda_model_kwargs(
    enable_flash_attn: bool = True, enable_multi_gpu: bool = False
) -> dict[str, Any]:
    """Build model_kwargs for CUDA loading.

    Args:
        enable_flash_attn: Whether to try Flash Attention 2 (requires
            the flash-attn package).
        enable_multi_gpu: Whether to shard across visible GPUs via
            device_map="auto".

    Returns:
        Dict suitable for passing as ``model_kwargs`` to
        SentenceTransformer / CrossEncoder.
    """
    kwargs: dict[str, Any] = {"torch_dtype": "auto"}
    if enable_multi_gpu and _has_multiple_gpus():
        kwargs["device_map"] = "auto"
    if enable_flash_attn and _flash_attn_available():
        kwargs["attn_implementation"] = "flash_attention_2"
    return kwargs


class RAGConfig:
    # Parent chunks: large context for LLM
    parent_chunk_size: int = 2500
    parent_chunk_overlap: int = 300
    # Child chunks: small for precise retrieval
    child_chunk_size: int = 250
    child_chunk_overlap: int = 50
    # Retrieval settings
    retrieve_k: int = 60  # Initial vector search: how many child chunks to fetch
    rerank_threshold: float = 0.1  # Minimum normalized rerank score (0-1) to keep
    max_parents: int = 10  # Max unique parent chunks passed to LLM
    rerank_candidate_k: int = RAG_RERANK_CANDIDATE_K
    rerank_batch_size: int = RAG_RERANK_BATCH_SIZE
    min_chunk_size: int = 50
    similarity_threshold: float = RAG_SIMILARITY_THRESHOLD
    embedding_model: str = RAG_EMBEDDING_MODEL
    top_k: int = RAG_TOP_K
    parser_type: str = "pymupdf"  # "pymupdf" (fast) or "docling" (detailed)
    max_num_pages: int = 200
    max_file_size: int = 104_857_600  # 100 MB
    # Parallel upload tuning: concurrent worker threads and flush sizes.
    # ``upload_workers`` — concurrent parser threads. PyMuPDF and
    # Docling release the GIL during hot paths, scaling to 4-8 cores.
    # ``index_flush_size`` — embed and insert every N files to bound
    # memory consumption during bulk uploads.
    upload_workers: int = 4
    index_flush_size: int = 50
    # Embedding model
    embedding_dim: int | None = RAG_EMBEDDING_DIM  # MRL truncation (None = full dim)
    embedding_instruction: str | None = (
        RAG_EMBEDDING_INSTRUCTION or None
    )  # Query instruction for Qwen3
    # Reranker model
    reranker_model: str = RAG_RERANKER_MODEL
    reranker_max_length: int = 2048
    # Instruction prepended to queries for domain-aware reranking
    reranker_instruction: str = (
        "You are ranking passages from life science, phytochemistry and biology research papers. "
        "Prioritize content about: genes, proteins, enzymes, metabolic pathways, molecular biology, "
        "cell biology, genetics, genomics, transcriptomics, proteomics, metabolomics, "
        "bioactive compounds, natural products, phytochemistry, plant extracts, "
        "analytical techniques (HPLC, GC-MS, NMR, sequencing), biological activity "
        "(antioxidant, antimicrobial, anti-inflammatory, cytotoxicity), "
        "medicinal plants, ethnobotany, and traditional medicine. "
        "Methods, results, and data-driven findings are highly relevant."
    )
    # Single shared local Qdrant DB. Per-user isolation happens via
    # collection naming (``user_<safe_user_id>_<8-char-hash>``) inside
    # this one directory, not via separate folders.
    #
    # Path resolution order:
    #   1. ``$QDRANT_DIR`` env var if set (with ``~`` expansion and
    #      resolution to an absolute path) — required on filesystems
    #      without working flock support (Lustre, some NFS configs).
    #   2. Default: ``<repo>/tmp/qdrant/`` (per-user temp, git-ignored).
    qdrant_dir: str = (
        os.path.abspath(os.path.expanduser(QDRANT_DIR))
        if QDRANT_DIR
        else os.fspath(tmp_dir() / "qdrant")
    )


config = RAGConfig()


class BloomIndexEmbeddings(_LCEmbeddings):
    """Single-model embeddings (``RAG_EMBEDDING_MODEL``).

    Inherits from ``langchain_core.embeddings.Embeddings`` so strict
    ``isinstance`` checks (``langchain-qdrant`` does one in
    QdrantVectorStore.__init__) pass.

    For Qwen3, queries use ``prompt_name="query"`` for instruction-aware
    retrieval; documents are encoded without prompts. A load failure
    raises — no silent model swap (mixed-model indexes corrupt
    retrieval).
    """

    def __init__(
        self,
        model: str = "Qwen/Qwen3-Embedding-4B",
        device: str | None = None,
        mrl_dim: int | None = None,
        query_instruction: str | None = None,
    ):
        self.device = device or get_optimal_device()
        self.mrl_dim = mrl_dim
        self.query_instruction = query_instruction
        self.model_name = model
        self.model_dim: int = 2560  # Qwen3-Embedding-4B default
        self._timing_local = threading.local()

        # Defer model loading to first use so RAGService construction
        # remains lightweight.
        self._model = None
        self._model_lock = threading.Lock()

    def begin_timing_session(self) -> None:
        self._timing_local.session = {"calls": 0, "total_ms": 0.0, "texts": 0}

    def consume_timing_session(self) -> dict[str, float]:
        session = getattr(self._timing_local, "session", None)
        self._timing_local.session = None
        if not session:
            return {"calls": 0, "total_ms": 0.0, "texts": 0}
        return session

    def _load_model(self, model_name: str):
        """Load the configured embedding model.

        For CUDA we enable fp16 (auto dtype) for speed and memory.
        For Apple MPS we keep fp32 because MPS fp16 support is still
        maturing. CPU always stays fp32.
        """
        from sentence_transformers import SentenceTransformer

        try:
            logger.info(f"Loading embedding model: {model_name} on {self.device}...")
            kwargs: dict[str, Any] = {"trust_remote_code": True}

            if self.device.startswith("cuda"):
                cuda_kwargs = _build_cuda_model_kwargs(
                    enable_flash_attn=RAG_FLASH_ATTENTION,
                    enable_multi_gpu=RAG_MULTI_GPU,
                )
                if "device_map" in cuda_kwargs:
                    # device_map="auto" manages its own placement;
                    # passing device= as well can trigger a conflict.
                    kwargs["model_kwargs"] = cuda_kwargs
                else:
                    kwargs["device"] = self.device
                    kwargs["model_kwargs"] = cuda_kwargs
                if _flash_attn_available():
                    logger.info("Flash Attention 2 enabled for embedding model.")
                if RAG_MULTI_GPU and _has_multiple_gpus():
                    try:
                        import torch as _torch

                        gpu_count = _torch.cuda.device_count()
                    except ImportError:
                        gpu_count = 0
                    logger.info(f"Multi-GPU enabled: sharding across {gpu_count} GPUs.")
            else:
                kwargs["device"] = self.device

            model = SentenceTransformer(model_name, **kwargs)
        except Exception as e:
            # If MPS fails, retry once on CPU before failing completely.
            if self.device != "mps":
                raise RuntimeError(f"Embedding model {model_name} failed to load: {e}")
            logger.info("Retrying embedding model load on CPU due to MPS failure...")
            try:
                model = SentenceTransformer(model_name, device="cpu", trust_remote_code=True)
            except Exception as cpu_e:
                raise RuntimeError(
                    f"Embedding model {model_name} failed to load (including CPU fallback): {cpu_e}"
                )
            self.device = "cpu"
            logger.info(f"Embedding model loaded (CPU fallback): {model_name} (device=cpu)")
            return self._finalize_model(model, model_name)

        return self._finalize_model(model, model_name)

    def _finalize_model(self, model, model_name: str):
        """Record dimension metadata for a loaded model."""
        self.model_name = model_name
        # Fall back across SentenceTransformer API versions:
        # get_embedding_dimension (v3+) vs
        # get_sentence_embedding_dimension (v2).
        # Resolve the callable before invocation so absent methods
        # do not raise TypeError during evaluation.
        dim_getter = getattr(model, "get_embedding_dimension", None) or getattr(
            model, "get_sentence_embedding_dimension", None
        )
        self.model_dim = (dim_getter() if dim_getter else None) or self.model_dim
        logger.info(
            f"Embedding model loaded: {model_name} "
            f"(dim={self.model_dim}, mrl_dim={self.mrl_dim or 'full'}, "
            f"instruction={'yes' if self.query_instruction else 'no'}, "
            f"device={self.device})"
        )
        return model

    def _ensure_model_loaded(self):
        """Lazy-load the embedding model on first use."""
        if self._model is not None:
            return
        with self._model_lock:
            if self._model is not None:
                return
            self._model = self._load_model(self.model_name)

    def _maybe_truncate(self, embeddings: list[list[float]]) -> list[list[float]]:
        """Truncate embeddings to MRL dimension if configured."""
        if self.mrl_dim and self.mrl_dim < self.model_dim:
            return [emb[: self.mrl_dim] for emb in embeddings]
        return embeddings

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of documents. No instruction prompt for documents."""
        self._ensure_model_loaded()
        started = time.perf_counter()
        embeddings = self._model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        ).tolist()
        elapsed_ms = (time.perf_counter() - started) * 1000
        session = getattr(self._timing_local, "session", None)
        if session is not None:
            session["calls"] += 1
            session["texts"] += len(texts)
            session["total_ms"] += elapsed_ms
        return self._maybe_truncate(embeddings)

    def embed_query(self, text: str) -> list[float]:
        """Embed a query. Uses instruction prompt for Qwen3 models.

        Qwen3-Embedding models ship with built-in prompt templates
        ("query"). If RAG_EMBEDDING_INSTRUCTION is set, it is used
        as a custom prompt. Non-Qwen models are encoded without
        prompts.
        """
        self._ensure_model_loaded()
        if "qwen" in self.model_name.lower():
            # Qwen3 models include built-in prompt_name="query".
            encode_kwargs = {
                "normalize_embeddings": True,
                "show_progress_bar": False,
                "convert_to_numpy": True,
            }
            if self.query_instruction:
                # Custom domain instruction overrides default template.
                encode_kwargs["prompt"] = self.query_instruction
            else:
                # Use the model's built-in "query" prompt template.
                encode_kwargs["prompt_name"] = "query"
            embeddings = self._model.encode([text], **encode_kwargs)
        else:
            embeddings = self._model.encode(
                [text],
                normalize_embeddings=True,
                show_progress_bar=False,
                convert_to_numpy=True,
            )
        result = embeddings[0].tolist()
        if self.mrl_dim and self.mrl_dim < self.model_dim:
            result = result[: self.mrl_dim]
        return result
