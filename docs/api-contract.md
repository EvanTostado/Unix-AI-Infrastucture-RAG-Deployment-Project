# Python Troubleshooting API Contract

Version: 1 (implementation target). This document specifies proposed behavior; it does not mean the endpoint is implemented.

## Endpoint and scope

`POST /api/ask`, with `Content-Type: application/json`. Responses use `application/json` and the envelope below. The browser calls the relative URL `/api/ask`; the frontend Nginx service will proxy it to the Python API.

Supported knowledge scope: Python built-in exceptions, tracebacks, types, functions, imports, files, and exception handling. Submitted code and tracebacks are input text and must never be executed. Third-party library troubleshooting is outside the agreed scope.

## Request

```json
{
  "question": "Why does combining this text and number produce a TypeError?",
  "code": "\"Age: \" + 25",
  "error": "TypeError: can only concatenate str (not \"int\") to str"
}
```

All three fields are required strings. Reject missing, null, non-string, and whitespace-only values. Check whitespace without altering code indentation or traceback line breaks. Reject unknown fields. Limits are initial application choices, configurable consistently in API and proxy:

| Field | Maximum characters |
| --- | ---: |
| question | 2,000 |
| code | 12,000 |
| error | 8,000 |

Limit the complete request body to 128 KiB. Malformed JSON returns 400; unsupported content type returns 415; oversized bodies return 413; invalid fields return 422. Validation failures do not trigger retrieval or inference.

## Response envelope

Every application response has `status`, `message`, `sections`, `sources`, and `metrics`.

| Field | Type | Meaning |
| --- | --- | --- |
| status | string | One value from the status table below. |
| message | string | User-facing explanation; empty string is permitted for success. |
| sections | array | Validated explanation sections; empty for non-answer outcomes. |
| sources | array | Retrieved context references; empty when no answer is returned. |
| metrics | object | Available timing/generation metrics, or an empty object. |

A successful answer contains exactly four ordered sections: `Meaning`, `Cause`, `Fix`, `Reasoning`. Each has a nonempty string `text` and its exact `title`. The Fix section may additionally contain a string `code`, preserving indentation. These are structured fields, not Markdown to be split after generation.

### Illustrative successful response

The example source below illustrates the schema only; it is not evidence of a completed retrieval.

```json
{
  "status": "ok",
  "message": "",
  "sections": [
    {"title": "Meaning", "text": "The operation combines incompatible types."},
    {"title": "Cause", "text": "The expression tries to concatenate a string and an integer."},
    {"title": "Fix", "text": "Convert the integer to a string.", "code": "\"Age: \" + str(25)"},
    {"title": "Reasoning", "text": "Both operands are now strings, so concatenation succeeds."}
  ],
  "sources": [
    {"chunk_id": "example-chunk-id", "file_name": "example-python-document.pdf", "title": "Example Python document"}
  ],
  "metrics": {}
}
```

## Sources and retrieval boundary

Use the existing PostgreSQL `embeddings` table: `id`, `text`, `metadata` (JSONB), and `embedding` (768-dimensional vector). Generate query embeddings using the same `nomic-embed-text` model used during ingestion. Model name, version, dimension, and preprocessing must remain compatible; changing the embedding model requires coordinated reindexing.

The retrieval implementation ranks chunks by cosine distance and applies a configurable relevance policy. Select an initial top-k (for example 4), then tune the relevance cutoff using representative troubleshooting questions; no universal threshold is assumed. Bound the supplied context to the generation model's context budget.

Map retrieved records to the current AI client as follows:

| Stored field | Internal AI-client field | Public source field |
| --- | --- | --- |
| id | chunk_id (new metadata field) | chunk_id |
| text | content | Not included in the source object |
| metadata.source | source | Do not expose the internal path |
| metadata.file_name | file_name | file_name |
| metadata.title | title | title |

`chunk_id` and `file_name` are required source strings; `title` is an optional nonempty string. Derive a safe basename from `metadata.source` when `file_name` is absent. Omit a blank title. Do not fabricate URLs, page numbers, titles, or source names. Current chunks do not reliably identify their PDF page, so page citations are outside version 1.

The backend constructs source objects from records actually supplied as model context; the model cannot invent them. Deduplicate by chunk ID and preserve retrieval order. Display the list as **Retrieved context**: it identifies supporting context supplied to the model, not independently verified per-sentence citations. An `ok` response requires at least one context source.

The API owns the flow: validate input, prepare a bounded query from question/code/error, embed it, retrieve context, call generation, validate its structured output, and assemble the response. The ingestion script remains a separate document preparation process.

## Status and HTTP behavior

| HTTP | status | Meaning |
| ---: | --- | --- |
| 200 | ok | Validated answer and retrieved context. |
| 200 | mock | Explicit development-only fixture; message must say that no AI/retrieval ran. |
| 200 | unsupported | Input is outside the agreed Python scope; no diagnosis returned. |
| 200 | insufficient_context | Available documentation does not support a grounded answer. |
| 400 | invalid_json | Request body is malformed JSON. |
| 413 | request_too_large | Complete body exceeds the configured byte limit. |
| 415 | unsupported_media_type | Request is not application/json. |
| 422 | invalid_request | Missing, invalid, unexpected, or over-limit fields. |
| 429 | rate_limited | Request was rate limited. |
| 502 | invalid_ai_response | Model/upstream output is malformed or violates the required answer schema. |
| 503 | unavailable | Database, embedding, or inference service is unavailable. |
| 504 | timeout | A required upstream operation exceeded its timeout. |
| 500 | internal_error | Unexpected application failure. |

Only `ok` and `mock` contain explanation sections. All other statuses return empty `sections` and `sources`, with a helpful message. A mock response uses empty `sources` and `metrics` and is enabled explicitly for development. It must never silently replace a failed live request. Use a meaningful Retry-After header for 429 when the rate limiter knows the delay.

```json
{
  "status": "unavailable",
  "message": "The AI assistant is temporarily unavailable. Please try again.",
  "sections": [],
  "sources": [],
  "metrics": {}
}
```

Normalize framework validation errors to this envelope. Configure proxy error responses to use it for this endpoint where practical; the frontend must also handle connection failures and non-JSON proxy responses with a clear generic message. Do not expose database credentials, internal paths, or exception traces in public messages.

## Metrics

Retain available metrics from the existing AI client: `ttft_seconds`, `tokens_per_second`, `total_latency_seconds`, and `tokens_generated`. All are nonnegative numbers; `tokens_generated` is an integer. Missing metrics are omitted, not invented. `ttft_seconds` is an estimate from model load plus prompt-evaluation durations because generation is non-streaming. `total_latency_seconds` is Ollama inference duration, not total browser request latency. Outcome belongs in top-level `status`, not a second status inside `metrics`.

## Frontend integration

Replace the sample provider call with POST /api/ask after the endpoint is available. Keep the existing textContent-based section rendering, add retrieved-context rendering, and update sample-specific labels. Preserve submitted values after errors and restore the enabled form in all completion paths. Inspect the response status and envelope rather than assuming every response is an answer.

## Acceptance checks

- The browser sends question, code, and error without losing whitespace.
- An explicit mock mode proves browser-to-Python communication and displays a mock notice.
- Invalid inputs return the documented envelope before any embedding or inference request.
- Successful live requests render the four ordered sections, optional fix code, and actual context sources.
- Out-of-scope inputs and insufficient documentation produce no fabricated diagnosis.
- Dependency failures, timeouts, rate limits, and malformed model output produce distinct documented outcomes.
- Submitted code is never executed and public sources contain no internal filesystem paths.

## Implementation order

1. Add request/response models and POST /api/ask with an explicit mock mode.
2. Add Nginx API proxy routing and connect frontend submission and rendering.
3. Adapt ai_client.py to Python scope and validate structured generation; preserve available metrics.
4. Add pgvector retrieval against the database branch schema and wire the live flow.
5. Verify the connected services on shared Docker networks before VM deployment.
