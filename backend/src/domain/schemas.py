"""Domain schemas and data contracts across API endpoints and pipelines.

Defines Pydantic models for named entity recognition (NER), chat RAG
queries, structured citation extraction, file indexing, and async
upload jobs.
"""

from typing import Any

from pydantic import BaseModel, Field


class NERRequest(BaseModel):
    """Request payload to extract named entities for a specific paper DOI."""

    doi: str = Field(..., min_length=1)


class Entity(BaseModel):
    """Extracted named entity mention with classification and metadata."""

    text: str
    label: str
    score: float
    # Normalized canonical form used for grouping and display.
    canonical: str | None = None
    preferred_name: str | None = None
    # All surface variations preserved for aggregation and counting.
    aliases: list[str] | None = None
    name_type: str | None = None
    linked_to: str | None = None
    scientific_name_verified: str | None = None
    accepted_scientific_name: str | None = None
    common_name: str | None = None
    inchikey: str | None = None
    smiles: str | None = None
    molecular_formula: str | None = None
    source_db: str | None = None
    source_url: str | None = None
    taxon_id: str | None = None
    match_status: str | None = None
    review_required: str | None = None


class NERResponse(BaseModel):
    """Extracted entities and text segment payload for an analyzed DOI."""

    doi: str
    # Indicates whether text represents full publication or abstract.
    mode: str
    text: str
    entities: list[Entity]


class ChatMessage(BaseModel):
    """Single message turn in a multi-turn conversation history."""

    role: str
    content: str


class QueryRequest(BaseModel):
    """User query payload for retrieval-augmented generation (RAG)."""

    query: str = Field(..., min_length=1)
    # Optional filter to restrict retrieval to specific document keys.
    selected_files: list[str] | None = None
    chat_history: list[ChatMessage] | None = None


class QueryResponse(BaseModel):
    """Synthesized answer and retrieved source contexts for a query."""

    answer: str
    sources: list[dict[str, Any]]


# --- Citation extraction schemas ---
#
# Structured output contract: the LLM outputs inline markers during
# streaming generation, followed by a JSON extraction pass validated
# against Citation models and verified against source text.


class Citation(BaseModel):
    """Verbatim quote and source chunk identifier supporting an answer claim."""

    chunk_id: str = Field(
        ...,
        description="Identifier of the source chunk supporting this claim.",
    )
    quote: str = Field(
        ...,
        description="Verbatim sentence or phrase from the cited chunk.",
    )


class Citations(BaseModel):
    """Top-level wrapper object for structured JSON citation extraction.

    Top-level object wrapping is required because several inference
    providers reject bare JSON arrays in structured-output modes.
    """

    citations: list[Citation] = Field(default_factory=list)


class IndexedFileInfo(BaseModel):
    """Metadata record for a document indexed in the knowledge base."""

    name: str
    file_type: str
    chunk_count: int
    indexed_at: str
    parser_type: str
    authors: str | None = None
    doi: str | None = None
    journal: str | None = None
    summary: str | None = None


class UploadResponse(BaseModel):
    """Response returned upon initiating or completing document uploads."""

    status: str
    message: str
    files: list[str]
    summaries: dict[str, str] | None = None
    # Population indicates background asynchronous processing.
    job_id: str | None = None


class UploadJobStatus(BaseModel):
    """Current processing state and execution result of an upload job."""

    job_id: str
    status: str
    message: str
    files: list[str]
    parser_type: str
    summaries: dict[str, str] | None = None
    error: str | None = None
    created_at: str
    completed_at: str | None = None
