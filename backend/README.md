# Python troubleshooting API

FastAPI implements `POST /api/ask` using the [version 1 contract](../docs/api-contract.md).
Requests and outgoing envelopes are validated with Pydantic. Submitted code and
tracebacks remain text; they are never executed. No database or AI client is
called in this step.

## Run with the web stack

From the repository root:

```bash
docker compose -f docker-compose.web.yml up -d --build
```

The backend Dockerfile uses Python 3.12, installs `requirements.txt`, copies the `backend` package under `/app`, and runs Uvicorn on `0.0.0.0:8001`. Web Nginx forwards `/api/` to `backend:8001`; only Nginx is published on host port 8080. Use `http://localhost:8080/api/ask` for the sample curl below when using Compose. In Codespaces, forward port 8080 for browser access.

Compose sets `API_MOCK_MODE: "true"`. The variable must contain underscores, not hyphens. After changing the environment, recreate the backend with `docker compose -f docker-compose.web.yml up -d --force-recreate backend`; after source changes use `docker compose -f docker-compose.web.yml up -d --build backend`.

## Run the API directly

This alternative exposes the API on host port 8001 without the frontend proxy. From the repository root, with Python 3.11 or newer:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
API_MOCK_MODE=true python -m uvicorn backend.app:app --host 127.0.0.1 --port 8001
```

Try a request from another terminal:

```bash
curl -i http://127.0.0.1:8001/api/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"Why does this fail?","code":"\"Age: \" + 25","error":"TypeError: incompatible types"}'
```

Mock mode returns HTTP 200 with `status: "mock"`, four ordered explanation
sections, and empty `sources` and `metrics`. Its notice explicitly states that no
AI/retrieval ran. The fixed TypeError example is independent of the submitted
values and is intended only to verify the API connection.

`API_MOCK_MODE` accepts `true` or `false` and defaults to `false`. With mock mode
disabled, valid requests return HTTP 503 with `status: "unavailable"` until the
live flow is implemented. There is no automatic fallback to mock mode.

The default complete body limit is 131072 bytes (128 KiB), including JSON syntax
and whitespace. Set `API_MAX_BODY_BYTES` to a positive integer to change it;
the current Nginx proxy instead enforces 120 KiB. This mismatch remains to be aligned: proxy-rejected bodies return an HTML 413 before API validation. The frontend error field also permits 12,000 characters versus the API limit of 8,000. Actual streamed bytes are counted
even without `Content-Length`. Field limits are 2000 characters for `question`,
12000 for `code`, and 8000 for `error`. Validation uses whitespace checks without
trimming any submitted value. JSON must be UTF-8.

Unsupported media types return 415, oversized JSON bodies return 413, malformed
JSON returns 400, and invalid fields return 422, all using the public envelope.
When multiple rules fail, media type is checked first, followed by body size,
JSON syntax, and then fields. Unexpected failures return a generic 500 envelope.
Unknown routes return 404 with `status: "not_found"`; unsupported methods return
405 with `status: "method_not_allowed"` and preserve the `Allow` header.
Optional section `code` and source `title` fields may be omitted, but explicit
`null` is rejected; OpenAPI describes them as optional strings.
Interactive schema documentation is available at `http://127.0.0.1:8001/docs`.

Run the contract checks:

```bash
python -m pip install -r backend/requirements-dev.txt
python -m pytest backend/tests -q
```

Frontend submission, Nginx routing, and the container mock path are connected. See the [integration check record](../docs/integration-checks.md). Structured AI generation, retrieval, and live orchestration remain future work. The Compose backend port is internal, so its `/docs` page is not directly available on host port 8001; use the direct API development command above for that page.
