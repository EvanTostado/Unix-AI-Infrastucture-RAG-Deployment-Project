# Web integration checks

Recorded: **2026-11-26 (UTC)**. Scope: the current Compose frontend → Nginx → FastAPI development mock path. Live AI inference, retrieval, and GCP deployment were not exercised. This record describes local working-tree code and running containers, not a release certification.

## Verified results

Read-only HTTP requests were sent to `http://127.0.0.1:8080`; Docker status showed both web services running and the frontend mapping `127.0.0.1:8080` to container port 80. In the coding-agent environment these checks required access outside the sandbox to reach the host Docker socket and localhost listener.

| Check | Observed result |
| --- | --- |
| `GET /` | HTTP 200; frontend page served. |
| `GET /app.js` | HTTP 200; served script contains `fetch('/api/ask', ...)`, confirming the current API submission code is in the running image. |
| `GET /sample-responses.js` | HTTP 200; example-input asset available. |
| Valid example `POST /api/ask` | HTTP 200, `status: "mock"`; Meaning, Cause, Fix, Reasoning in order; empty sources and metrics. |
| Different valid code (`print(1)`) | HTTP 200, `status: "mock"`; mock mode also accepts valid inputs other than the loaded example. |
| Whitespace-only question | HTTP 422, `status: "invalid_request"`. |
| Malformed JSON (`{`) | HTTP 400, `status: "invalid_json"`. |
| `Content-Type: text/plain` | HTTP 415, `status: "unsupported_media_type"`. |
| Body of 122,881 bytes | HTTP 413, `Content-Type: text/html`, from the Nginx 120 KiB body limit. |
| `node --check frontend/app.js` | Passed syntax check. |
| `docker compose -f docker-compose.web.yml config --quiet` | Passed Compose configuration validation. |
| Backend test suite | Not run: `.venv/bin/python -m pytest backend/tests -q -p no:cacheprovider` failed because `pytest` is not installed in this environment. |

Earlier troubleshooting confirmed that `API-MOCK-MODE` left mock mode disabled and produced HTTP 503 with `status: "unavailable"`. The corrected Compose key is `API_MOCK_MODE`; the current HTTP check confirms mock mode is enabled. The user also confirmed that manually forwarding port 8080 in Codespaces resolved page access. A complete browser submission/rendering test has not been independently verified in this record.

## Repeat the API check

Start or rebuild the stack from the repository root:

```bash
docker compose -f docker-compose.web.yml up -d --build
docker compose -f docker-compose.web.yml ps
curl -i http://127.0.0.1:8080/api/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"Why does this fail?","code":"\"Age: \" + 25","error":"TypeError: incompatible types"}'
```

Expect HTTP 200, `status: "mock"`, and a message explicitly stating that no AI/retrieval ran. The fixed explanation is independent of the request contents. Stop the stack with `docker compose -f docker-compose.web.yml down` when finished.

For backend contract tests, install the development dependencies in a virtual environment first, as described in the [backend README](../backend/README.md):

```bash
python -m pip install -r backend/requirements-dev.txt
python -m pytest backend/tests -q
```

## Browser checklist to complete manually

Use `http://localhost:8080` on the Docker host or the Codespaces forwarded port 8080 URL. Do not open the HTML directly or use a separate static preview server. Rebuild after edits and hard-refresh before checking.

- [ ] Select **Load example → Get explanation**; confirm a POST to `/api/ask` appears in developer tools with HTTP 200 and `status: "mock"`.
- [ ] Confirm the mock notice, four explanation sections, and fix code display correctly.
- [ ] Change the code to a different valid value; confirm the same fixed mock explanation appears without implying a live diagnosis.
- [ ] Use multiline indented code and inspect the request JSON to confirm whitespace is preserved.
- [ ] Confirm loading disables both buttons, makes fields read-only, and restores controls after completion.
- [ ] Confirm blank input is rejected before submission.
- [ ] Submit an error longer than 8,000 characters; confirm the API validation message appears and inputs remain available for correction.
- [ ] In browser developer tools, block `/api/ask` to simulate a network failure; confirm the generic error message appears, inputs are preserved, and controls recover. Remove the block afterward.

These items are pending browser checks, not assertions that they have passed.

## Known integration gaps

- Nginx permits 120 KiB, while the API contract/API default permits 128 KiB. Nginx rejects larger bodies first and returns HTML; the browser catches the failed JSON parsing and shows its generic error message.
- The error textarea permits 12,000 characters while API validation permits 8,000; the API rejects over-limit values with HTTP 422.
- The frontend renders messages and sections but does not yet validate the complete response envelope or render retrieved-context sources. Some labels still describe local sample content.
- The web Compose file has no readiness health check or startup dependency. If proxy startup or connectivity fails, inspect both service logs rather than assuming the containers are ready from their running state alone.
- Live generation, PostgreSQL/pgvector retrieval, AI-tier networking with the API, and cloud deployment remain future work.
