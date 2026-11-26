# Python troubleshooting frontend

Plain HTML, CSS, and JavaScript provide a form connected to the FastAPI backend through Nginx. **The default Compose stack uses a development mock:** every valid request returns the same TypeError example, regardless of the submitted values. No AI, retrieval, or submitted-code execution runs.

## Start the connected frontend

Install Docker with the Docker Compose plugin. Run from the repository root:

```bash
docker compose -f docker-compose.web.yml up -d --build
docker compose -f docker-compose.web.yml ps
```

Open `http://localhost:8080`. Select **Load example**, then **Get explanation**. Expect a development mock notice and Meaning, Cause, Fix, and Reasoning sections, with the fix code rendered separately. The API returns HTTP 200 with `status: "mock"`.

In **GitHub Codespaces**, open the **Ports** tab. If 8080 is absent, click **Forward a Port**, enter `8080`, then click **Open in Browser** for that row. Use the forwarded URL instead of your computer's localhost. Ports 3000 and 3001 do not correspond to the web service in this Compose file.

Use this Nginx-served page for testing. Opening `index.html` directly or using Live Preview/Live Server or a Python static server does not supply the `/api/ask` backend proxy. The relative fetch URL follows the page's origin, including a Codespaces forwarded origin.

## Rebuild after edits

The Dockerfile copies frontend assets into the image; there is no source bind mount or hot reload. After editing HTML, CSS, JavaScript, or Nginx configuration:

```bash
docker compose -f docker-compose.web.yml up -d --build frontend
```

Then hard-refresh: **Ctrl+Shift+R** on Windows/Linux, **Command+Shift+R** on Mac. Rebuild both services with the startup command if backend code also changed. Stop with `docker compose -f docker-compose.web.yml down`.

## Behavior and files

The form checks for blank fields, preserves submitted strings in the JSON request, and posts `question`, `code`, and `error` to `/api/ask`. While waiting it disables buttons and makes fields read-only. It displays the API message and sections using `textContent`; errors preserve inputs and the `finally` block restores controls. Network failures and non-JSON responses show “Could not retrieve a response. Please try again.” JSON API errors display the server message.

| File | Purpose |
| --- | --- |
| `index.html` | Form, development mock notice, answer panel, and static sources placeholder. |
| `styles.css` | Responsive layout and code formatting. |
| `app.js` | Example loading, validation, API POST, loading state, and section rendering. |
| `sample-responses.js` | Supplies `example` values to Load example. Its legacy `respond()` function remains but the form no longer calls it. |
| `nginx.conf` | Serves static files and forwards `/api/` unchanged to `http://backend:8001`. |
| `Dockerfile` | Builds the Nginx image with configuration and static assets. |

The backend is reachable by service name on the Compose network; host port 8080 maps to Nginx port 80. The backend does not need a host port mapping for the browser flow. Ollama and a database are not required for mock mode.

## Troubleshooting

- **Browser cannot connect:** check `docker compose -f docker-compose.web.yml ps` and the Codespaces 8080 forward. Inspect logs with `docker compose -f docker-compose.web.yml logs --tail 50 frontend backend`.
- **Old UI or JavaScript:** rebuild the frontend and hard-refresh. Check the Network tab's `/app.js` response for the current fetch code.
- **Generic retrieval error:** confirm the page uses the Compose/forwarded URL. In browser developer tools, inspect the `/api/ask` request URL, HTTP status, and response. HTML proxy errors also trigger this message because JSON parsing fails.
- **HTTP 503 with `unavailable`:** ensure Compose uses `API_MOCK_MODE: "true"`, with underscores. Apply an environment change with `docker compose -f docker-compose.web.yml up -d --force-recreate backend`.
- **HTTP 502:** inspect backend startup and frontend proxy logs; Compose does not define a readiness health check or startup dependency. If Nginx fails to resolve `backend` at startup, check that the backend is running and restart the frontend.

## Integration checks and remaining work

See the [dated integration record and repeatable checks](../docs/integration-checks.md). The HTTP path and current served JavaScript were checked; the full browser interaction checklist is recorded separately for manual confirmation.

The error textarea currently permits 12,000 characters while the API accepts 8,000, so longer errors receive HTTP 422. Nginx limits bodies to 120 KiB while the API permits 128 KiB; larger bodies can receive an HTML 413 from Nginx. Sources rendering and some sample-specific page labels remain unfinished. These are documented limitations, not changes made by this documentation update.
