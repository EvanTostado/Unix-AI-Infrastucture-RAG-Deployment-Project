# AI tool and prompts used

This file is copied into every `tests/results/<run>/run_record.md`.
Add a row to the table at the bottom whenever you (or an AI tool) change the tests or run them.

## Tool

| Item | Value |
|---|---|
| AI tool | Claude (Anthropic), claude.ai chat |
| Model | Claude Sonnet 5.5 |
| Date built | 2026-11-28 |
| Inputs given to the AI | `ai-tier/` source files only: `nginx-ai.conf`, `docker-compose.ai.yml`, `ai_client.py`, `.gitignore` |
| Secrets given to the AI | **None.** No passwords, keys, tokens or service-account files were shared. |

## Prompt 1 - building the tests (verbatim)

> AI load test: send concurrent requests to the AI endpoint at 1, 5, and 10 concurrent
> users (e.g., ab -p with a JSON body, hey, k6, or locust). Record latency (p50/p95),
> tokens per second, and error rate; chart the results and identify the bottleneck.
> AI failover: stop or kill one inference container (and/or VM) during active requests.
> Pass if requests keep succeeding through the remaining replica, the health check
> removes the failed replica, and it recovers automatically.
> Rate-limit test: send a burst of requests to the AI endpoint. Pass if HTTP 429
> responses are returned and the service remains healthy.
> Safety: disruptive tests (e.g., failover) run only when explicitly requested. Do not
> give the AI tool passwords, keys, tokens, or service account key files, and review
> each command before an AI tool runs it.
>
> can you make these tests follow this
>
> Use a generative AI tool (e.g., Claude, ChatGPT, or Gemini) to build the tests below.
> Tests are separate from the setup scripts and may be run either:
> o from a test script committed to GitHub (e.g., tests/run_tests.sh), or
> o by an AI tool that runs the tests for you (e.g., Claude Code, ChatGPT, or Gemini CLI executing commands).
> Document every test run (required for both options): the AI tool and prompt(s) used, the commands that
> were run, the date/time, the output, and a pass/fail result for each check. Commit the test logs to
> GitHub (e.g., tests/results/).

## Result of Prompt 1

`tests/run_tests.sh` (runner + safety gate), `tests/ai_tests.py` (preflight, load, ratelimit, failover, report),
`tests/README.md`, and this file. The AI tool read the nginx config and compose file so thresholds match the real
setup (5 r/s + burst 10 limiter, 2 replicas x `OLLAMA_NUM_PARALLEL=2` = 4 inference slots, 2-CPU replica limit,
`max_fails=2 fail_timeout=10s`).

## Human review log

Fill this in before committing results. Every command an AI tool runs must be reviewed by a human first.

| Date (UTC) | Who ran it | AI tool involved | Commands reviewed by | Notes |
|---|---|---|---|---|
| 11/28/26   | Evan T     | Claude           | Evan T|---|
