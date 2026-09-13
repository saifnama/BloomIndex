# API Reference

This document describes the BloomIndex REST API.

The backend serves the API on port 8000. In production it also serves the built frontend from the same port.

---

# Interactive Documentation

FastAPI generates a live reference from the running app:

```text
Swagger UI:  http://localhost:8000/docs
OpenAPI JSON: http://localhost:8000/openapi.json
```

---

# Conventions

* Base URL: `http://localhost:8000`
* No authentication. A session cookie identifies each chat user.
* Responses are JSON unless stated otherwise.
* Errors use standard HTTP status codes with a JSON body.
* Routes named `*/json` return JSON. They may accept form or JSON input. Each section shows the format.
* `GET /{full_path}` is the SPA fallback. It serves the built frontend.

---

# Endpoint Overview

## Health

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/health/ready` | Readiness: `ready`, `degraded`, or `down` (503) |

## Search

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/search/json` | Search Europe PMC and OpenAlex |
| GET | `/search/types` | List article-type filter values |

## Papers

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/paper/json` | Fetch a paper by DOI and analyse it |
| POST | `/paper/section/json` | Switch the visible section |
| GET | `/paper/pdf` | Download a paper PDF |
| GET | `/paper/pdf-proxy` | Proxy an external PDF |
| GET | `/paper/db/list` | List knowledge-base papers |
| GET | `/paper/db/{doi}/entities` | List entities of one knowledge-base paper |
| GET | `/doi/abstract` | Fetch an abstract by DOI |

## NER

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/ner/process` | Extract entities from raw text |
| POST | `/ner/doi/json` | Fetch a paper by DOI and extract entities |
| POST | `/ner/upload/json` | Upload a PDF for entity extraction |
| GET | `/ner/uploaded/{stored_filename}` | View an uploaded PDF |
| DELETE | `/ner/uploaded/{stored_filename}` | Delete an uploaded PDF |
| DELETE | `/ner/cache/{doi}` | Clear the NER cache for one DOI |

## Chat

All chat routes start with `/api/chat`.

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/chat/upload/json` | Upload PDFs and index them |
| GET | `/api/chat/upload/jobs` | List upload jobs for the user |
| GET | `/api/chat/upload/status/{job_id}` | Get one upload job |
| GET | `/api/chat/files/json` | List indexed files |
| GET | `/api/chat/files/{filename}/content` | Get extracted text |
| GET | `/api/chat/files/{filename}/markdown` | Get extracted markdown |
| DELETE | `/api/chat/files/{filename}` | Delete one indexed file |
| POST | `/api/chat/query/json` | Ask one question, get the full answer |
| POST | `/api/chat/query/stream` | Ask one question, stream the answer |
| POST | `/api/chat/suggest` | Suggest follow-up questions |
| POST | `/api/chat/reset` | Reset the user's RAG data |
| POST | `/api/chat/cleanup` | Clean up user files and jobs |

## Dashboard

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/dashboard/metrics` | Knowledge-base statistics |

---

# Example Requests

## Search

`POST /search/json` accepts form fields:

| Field | Type | Purpose |
|-------|------|---------|
| `query` | string | Keyword, DOI, PMID, or PMCID |
| `source` | string | `europe-pmc` or `openalex` |
| `open_access` | boolean | Open-access filter |
| `has_full_text` | boolean | Full-text filter |
| `article_type` | string | Research or review filter |
| `sort` | string | `relevance`, `cited`, or `date` |
| `page` | integer | Page number |
| `cursor_mark` | string | Deep-pagination cursor |

```bash
curl -X POST http://localhost:8000/search/json \
  -d "query=eugenol" -d "sort=cited"
```

## Analyse a Paper

`POST /paper/json` accepts form fields:

| Field | Type | Purpose |
|-------|------|---------|
| `doi` | string | Required. Paper DOI |
| `run_ner` | boolean | Run entity extraction |
| `source` | string | Preferred source |

```bash
curl -X POST http://localhost:8000/paper/json \
  -d "doi=10.1186/s12951-018-0334-5" -d "run_ner=true"
```

## Extract Entities from Text

`POST /ner/process` accepts JSON:

```bash
curl -X POST http://localhost:8000/ner/process \
  -H "Content-Type: application/json" \
  -d '{"text": "Eugenol in Ocimum tenuiflorum shows antioxidant activity."}'
```

## Ask a Question

`POST /api/chat/query/json` accepts JSON:

```json
{
  "query": "Which species produce eugenol?",
  "selected_files": [],
  "chat_history": []
}
```

The response contains `answer` and `sources`.

```bash
curl -X POST http://localhost:8000/api/chat/query/json \
  -H "Content-Type: application/json" \
  -d '{"query": "Which species produce eugenol?"}'
```

---

# Streaming Format

`POST /api/chat/query/stream` streams newline-delimited JSON (`application/x-ndjson`). Each line is one frame.

| Frame Type | Purpose |
|------------|---------|
| `text_delta` | One answer text chunk |
| `sources` | Citation sources, sent before the text |
| `answer_corrected` | Final answer text |
| `error` | Error message |
| `done` | End of stream |

Example stream:

```text
{"type": "sources", "sources": [...]}
{"type": "text_delta", "text": "Eugenol comes from "}
{"type": "text_delta", "text": "Ocimum tenuiflorum [c1]."}
{"type": "done"}
```

---

# Core Schemas

## QueryRequest

| Field | Type | Required | Purpose |
|-------|------|----------|---------|
| `query` | string | yes | The question |
| `selected_files` | string array | no | Restrict search to these files |
| `chat_history` | ChatMessage array | no | Earlier turns |

## Entity

`NERResponse.entities` returns items with these key fields:

| Field | Purpose |
|-------|---------|
| `text` | Surface form in the text |
| `label` | Entity type, such as `CHEMICAL` |
| `score` | Confidence between 0 and 1 |
| `canonical` | Canonical form |
| `preferred_name` | Linked preferred name |
| `inchikey`, `smiles`, `molecular_formula` | Chemical identifiers |
| `taxon_id` | Species identifier |
| `source_db`, `source_url` | Link-out to the reference database |

## UploadJobStatus

| Field | Purpose |
|-------|---------|
| `job_id` | Job identifier |
| `status` | `processing`, `completed`, or `failed` |
| `files` | File names in the job |
| `parser_type` | `pymupdf` or `docling` |
| `error` | Failure message, if any |

---

# Limits

* The PDF proxy accepts only known academic hosts and caps downloads at 20 MB.
* Uploaded PDFs follow the same size limit.
* The chat user lock times out after 60 seconds. A busy user receives a 503 response.
