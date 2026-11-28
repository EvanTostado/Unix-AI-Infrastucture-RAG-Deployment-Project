# AI tier tests

Tests for the Ollama + nginx AI tier (`docker-compose.ai.yml`, `nginx-ai.conf`). They are separate from the
setup scripts and were built with a generative AI tool (see [PROMPTS.md](PROMPTS.md)).

```
tests/
├── run_tests.sh      runner: safety gate, logging, run record
├── ai_tests.py       the tests (Python stdlib; matplotlib optional for charts)
├── PROMPTS.md        AI tool + prompts used, human review log
├── README.md
└── results/<UTC timestamp>/    one folder per run - COMMIT THESE
    ├── run_record.md           tool, prompts, commands, date/time, output, PASS/FAIL per check
    ├── commands.txt  environment.txt  *.log  *.json
    └── load_results.csv  load_chart.png  failover_requests.csv  failover_timeline.png
```

## Run

Run on the VM that hosts the stack (the LB only listens on `127.0.0.1:11434`), from the repo root:

```bash
pip install matplotlib            # optional, only for charts (CSV/JSON are written either way)
chmod +x tests/run_tests.sh

./tests/run_tests.sh              # safe suite: preflight + load + ratelimit
./tests/run_tests.sh load         # one test (preflight always runs first)
./tests/run_tests.sh failover     # DISRUPTIVE - see Safety
```

Then commit the log: `git add tests/results && git commit -m "AI tier test run"`.
Exit code is non-zero if any check FAILs. `WARN` and `SKIP` never fail a run.

The load test is real CPU inference: with the defaults (20 requests per level, 64 tokens) expect roughly
5-10 minutes. For a quick pass use `LOAD_REQUESTS=10 NUM_PREDICT=32 ./tests/run_tests.sh load`.

## What is tested and when it passes

| Test | What it does | PASS when |
|---|---|---|
| **preflight** (P1-P5) | Read-only: endpoint up, model staged, 2 replicas + `ai-lb` running, one real generation works | all pass; otherwise the run stops |
| **load** (L1-L6) | 1, 5, 10 concurrent users (closed loop) on `POST /api/generate`; records p50/p95 latency, tokens/s (per request from Ollama's `eval_count/eval_duration`, and aggregate), error rate, and `docker stats` CPU/memory; writes CSV + 4-panel chart; names the likely bottleneck with its evidence | every level has successes, error rate <= 5% per level, metrics + chart written, containers still running |
| **ratelimit** (R1-R5) | 60 simultaneous requests released at once | some HTTP 429; accepted count <= burst + 1 + rate x window (+2); no 5xx/connection errors among accepted; a normal request succeeds after cooldown; containers running with no restarts |
| **failover** (F1-F10) | 3 steady users; baseline; `docker kill` one replica mid-traffic; watch Docker state/health; keep traffic through recovery | see below |

Failover checks: F2 baseline healthy (otherwise **nothing is killed**) - F3 kill succeeded - **F4 requests started
after the kill succeed >= 95% via the remaining replica** - F5 requests that were mid-flight on the dead replica
(WARN if lost) - **F6 failure detected** (Docker state/healthcheck) - **F7 nginx stops routing to it** (upstream error
lines in `ai-lb` logs stay within `max_fails` + one probe per `fail_timeout`) - **F8 automatic recovery** (healthy
again with `RestartCount` incremented, no manual action) - **F9 recovered replica serves client traffic again** (its
logs show new `/api/generate` calls) - F10 both replicas running + healthy and the last requests succeed.

Load-test bottleneck logic (heuristic - read the evidence lines): rate limiter (any 429) > backend errors/timeouts >
memory >= 90% of limit > CPU (avg >= 80% or peak >= 90% of the replica limit) > slot queueing (c above
replicas x `OLLAMA_NUM_PARALLEL` and p50 grows >= 1.5x) > none reached.

## Settings (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `AI_BASE_URL` | `http://127.0.0.1:11434` | endpoint under test |
| `CHAT_MODEL` | `qwen2.5:1.5b` | model name |
| `LOAD_LEVELS` / `LOAD_REQUESTS` | `1,5,10` / `20` | concurrency levels / requests per level |
| `NUM_PREDICT` | `64` | max tokens per load-test request (keeps runs comparable) |
| `MAX_ERROR_PCT` | `5` | load-test error-rate threshold |
| `INFERENCE_SLOTS` / `REPLICA_CPU_LIMIT` | `4` / `2.0` | for bottleneck analysis (replicas x `OLLAMA_NUM_PARALLEL`, compose CPU limit) |
| `RL_BURST_REQUESTS` / `RL_RATE` / `RL_BURST` | `60` / `5` / `10` | burst size; nginx `rate=` and `burst=` |
| `FO_BASELINE` / `FO_POST_WINDOW` / `FO_RECOVERY_TIMEOUT` | `20` / `30` / `240` s | failover timing |
| `FO_MIN_SUCCESS_PCT` / `FAILOVER_TARGET` | `95` / first replica | pass threshold / container to kill |
| `AI_TOOL`, `AI_MODEL`, `RUN_BY`, `PROMPTS_FILE` | see script | recorded in the run record |

Replicas are found by compose label (`com.docker.compose.service=ollama`), so the container names don't have to be
`ai-tier-ollama-N`. The failover test refuses to kill anything that is not one of those replicas.

## Safety

- **Disruptive tests only on explicit request.** `failover` needs a human to type `kill-replica`. In a
  non-interactive shell (which is what Claude Code, Gemini CLI, etc. use) it refuses unless `--yes` is passed.
  **An AI tool must never add `--yes` itself** - it has to ask you first. `ai_tests.py failover` also refuses
  without `--confirm-disruptive`. Failover does not run as part of the default suite.
- **No secrets.** The tests need none: no passwords, keys, tokens or service-account files are read, passed to an AI
  tool, or logged. `docker inspect` is only ever called with a format that returns state / health / restart count (never
  the container environment), and the environment snapshot holds version strings and `docker ps` output only.
- **Review every command.** Every host command is echoed as `$ ...` in the logs and listed in `run_record.md`. When
  using an AI tool to run the tests, read each proposed command before approving it, and record that in the review
  log in [PROMPTS.md](PROMPTS.md).
- The rate-limit test fires a burst on purpose; it is not destructive, but don't run it against a shared or
  production endpoint.

## Running with an AI tool (Claude Code / Gemini CLI / ChatGPT)

Use the prompt template at the bottom of [PROMPTS.md](PROMPTS.md) and set `RUN_BY` so the run record says who ran
it, e.g. `RUN_BY="Claude Code executing tests/run_tests.sh" ./tests/run_tests.sh`. The record is generated the same
way for script runs and AI-run runs.

## Known risks in the current config (what these tests are likely to surface)

Reading `nginx-ai.conf` and `docker-compose.ai.yml` against the pass criteria, expect these - they are findings, not
test bugs:

1. **In-flight requests are lost on failover (F5 WARN).** nginx does not retry a `POST` that was already sent to
   an upstream unless `proxy_next_upstream` includes `non_idempotent`. Requests that *start* after the kill are
   retried safely (F4), but the 1-3 being generated on the dead replica at that moment return 502. To fix, add
   `non_idempotent` to `proxy_next_upstream` (the client then re-runs the generation on the other replica).
2. **Recovered replica may never get traffic again (F9).** Open-source nginx resolves `ai-tier-ollama-N` once at
   startup. If Docker gives the restarted container a different IP, nginx keeps using the old one. (It will also
   refuse to start at all if a replica is down at that moment.) Fixes: pin static IPs for the replicas, reload nginx
   after a replica restart, or use a `resolver 127.0.0.11` with variable-based `proxy_pass`.
3. **The healthcheck is expensive and doesn't remove anything.** `ollama run qwen2.5:1.5b 'hi'` is a full
   generation every 30s on each replica, inside the same 2-CPU limit as client traffic, with a 15s timeout - under
   load it can flip a healthy replica to `unhealthy` (L6 WARN) and it adds to the CPU bottleneck. Also, Docker only
   *labels* a container unhealthy; what actually takes a replica out of rotation is nginx's passive check
   (`max_fails=2 fail_timeout=10s`), which F7 measures. A cheaper check such as `["CMD", "ollama", "list"]` avoids
   both problems.
4. **`docker stop` will not auto-recover** because `restart: always` honors a manual stop. The test uses
   `docker kill`, which Docker does restart, so "recovers automatically" is actually exercised.

## Why Python rather than `ab` / `hey` / `k6`

The brief allows any of them. `ab`/`hey` only see HTTP timing; tokens per second lives in Ollama's JSON response
(`eval_count`, `eval_duration`), and the same code path is reused for the rate-limit and failover checks, so one
dependency-free script covers everything. Using `hey` for the 429 burst or `k6` for the load levels is still fine -
just record the commands and output in a run record the same way.
