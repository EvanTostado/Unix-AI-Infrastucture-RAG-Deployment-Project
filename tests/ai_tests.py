#!/usr/bin/env python3
"""
AI-tier tests for the Ollama + nginx stack (docker-compose.ai.yml).

Sub-commands
  preflight   read-only sanity checks (endpoint, model, replicas)
  load        1 / 5 / 10 concurrent users -> p50/p95, tokens/sec, error rate,
              CSV + chart + bottleneck analysis
  ratelimit   burst of requests -> expect HTTP 429 and a healthy service
  failover    DISRUPTIVE: kills one inference container during live traffic
              (refuses to run without --confirm-disruptive)
  report      builds run_record.md from the JSON/log files in --outdir

Design notes
  * Python stdlib only (matplotlib optional, for charts). Python's http client is
    used instead of ab/hey/k6 because we need Ollama's own timing fields
    (eval_count / eval_duration) to compute tokens per second.
  * No credentials are needed or read. The only host commands run are listed in
    the output (look for lines starting with "$ ").
  * Every check ends as PASS / FAIL / WARN / SKIP. Only FAIL makes a test fail.
"""
import argparse
import csv
import json
import math
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone

DEFAULT_PROMPT = "In three short sentences, explain what an LVM logical volume is."
CMD_LOG = []  # every host command run, saved into the test's JSON


# --------------------------------------------------------------------------- helpers
def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg=""):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


def env(name, default, cast=str):
    v = os.environ.get(name)
    return cast(v) if v not in (None, "") else default


def clean(o):
    """NaN/inf -> None so the JSON we write is valid."""
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    return o


def sh(cmd, timeout=60, quiet=False):
    """Run a host command (no shell). Returns (rc, stdout, stderr). Commands are logged."""
    line = " ".join(shlex.quote(c) for c in cmd)
    if not quiet:
        log("$ " + line)
        CMD_LOG.append(f"{utcnow()}  $ {line}")
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except FileNotFoundError:
        return 127, "", f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def have_docker():
    return shutil.which("docker") is not None


def pctl(values, p):
    if not values:
        return float("nan")
    s = sorted(values)
    k = (len(s) - 1) * p / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    return s[int(k)] if lo == hi else s[lo] + (s[hi] - s[lo]) * (k - lo)


def mean(values):
    return sum(values) / len(values) if values else float("nan")


def fmt(x, nd=2):
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{nd}f}"


# --------------------------------------------------------------------------- HTTP
def http(method, url, body=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    t_wall, t_mono = time.time(), time.monotonic()
    out = dict(status=0, headers={}, body=b"", error=None, t_start=t_wall)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out.update(status=r.status, headers=dict(r.headers), body=r.read())
    except urllib.error.HTTPError as e:
        try:
            body_bytes = e.read()
        except Exception:
            body_bytes = b""
        out.update(status=e.code, headers=dict(e.headers or {}), body=body_bytes, error=f"HTTP {e.code}")
    except Exception as e:  # timeouts, refused, reset, ...
        out.update(error=f"{type(e).__name__}: {e}")
    out["t_end"] = time.time()
    out["latency"] = time.monotonic() - t_mono
    return out


def generate(a, num_predict=None, timeout=None):
    """One non-streaming /api/generate call through the load balancer."""
    body = {
        "model": a.model,
        "prompt": a.prompt,
        "stream": False,
        "options": {"temperature": 0, "seed": 42, "num_ctx": 2048,
                    "num_predict": num_predict or a.num_predict},
    }
    r = http("POST", a.base_url + "/api/generate", body, timeout or a.timeout)
    r.update(ok=False, eval_count=0, tps=0.0)
    if r["status"] == 200:
        try:
            d = json.loads(r["body"])
            ec, ed = d.get("eval_count", 0), d.get("eval_duration", 0)
            r.update(ok=True, eval_count=ec, tps=(ec / (ed / 1e9)) if ed else 0.0)
        except ValueError:
            r["error"] = "invalid JSON in 200 response"
    r.pop("body", None)
    return r


def req_line(tag, r):
    err = f"  ERR {r['error']}" if r.get("error") else ""
    return (f"  {tag} HTTP {r['status']:<3} {r['latency']:6.2f}s "
            f"tokens={r['eval_count']:<4} tps={r['tps']:5.1f}{err}")


def run_pool(a, concurrency, total, num_predict=None, tag="pool"):
    """Closed-loop users: `concurrency` threads pull from `total` requests."""
    lock, counter, results = threading.Lock(), {"n": 0}, []
    barrier = threading.Barrier(concurrency)

    def worker(user):
        barrier.wait()
        while True:
            with lock:
                if counter["n"] >= total:
                    return
                idx = counter["n"]
                counter["n"] += 1
            r = generate(a, num_predict=num_predict)
            r.update(idx=idx, user=user)
            with lock:
                results.append(r)
            log(req_line(f"{tag} c={concurrency} #{idx:03d} u{user:02d}", r))

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(concurrency)]
    t0 = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results, time.monotonic() - t0


# --------------------------------------------------------------------------- docker helpers
def discover_replicas(a):
    rc, out, err = sh(["docker", "ps", "--filter", f"label=com.docker.compose.service={a.service}",
                       "--format", "{{.Names}}"])
    if rc != 0:
        log(f"  docker ps failed: {err}")
        return []
    return sorted(n for n in out.splitlines() if n.strip())


def inspect_state(name):
    """(status, health) e.g. ('running', 'healthy'). Only reads two fields (no env/secrets)."""
    fmt_s = "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}"
    rc, out, _ = sh(["docker", "inspect", "-f", fmt_s, name], quiet=True)
    if rc != 0 or "|" not in out:
        return "missing", "none"
    s, h = out.split("|", 1)
    return s, h


def restart_count(name):
    rc, out, _ = sh(["docker", "inspect", "-f", "{{.RestartCount}}", name], quiet=True)
    try:
        return int(out)
    except ValueError:
        return None


def docker_logs(name, since_epoch):
    """Combined stdout+stderr lines of a container since a unix time (None on failure). Command is logged."""
    # Exact instant (RFC3339, microseconds): the killed process cannot log after this, so anything newer
    # in the log was written by the restarted container / by nginx reacting to the kill.
    since = datetime.fromtimestamp(since_epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    cmd = ["docker", "logs", "--since", since, name]
    line = " ".join(shlex.quote(c) for c in cmd)
    log("$ " + line)
    CMD_LOG.append(f"{utcnow()}  $ {line}")
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (p.stdout + "\n" + p.stderr).splitlines() if p.returncode == 0 else None


class Report:
    def __init__(self, test):
        self.test, self.checks, self.metrics = test, [], {}
        self.started = utcnow()

    def check(self, cid, desc, status, detail=""):
        self.checks.append(dict(id=cid, description=desc, status=status, detail=detail))
        log(f"[{status}] {cid}: {desc}" + (f"  -- {detail}" if detail else ""))

    def overall(self):
        st = [c["status"] for c in self.checks]
        if "FAIL" in st:
            return "FAIL"
        return "PASS" if "PASS" in st else "SKIP"

    def save(self, outdir):
        res = self.overall()
        os.makedirs(outdir, exist_ok=True)
        with open(os.path.join(outdir, f"{self.test}.json"), "w") as f:
            json.dump(clean(dict(test=self.test, started=self.started, ended=utcnow(), result=res,
                                 checks=self.checks, metrics=self.metrics, commands=CMD_LOG)), f, indent=2)
        log(f"RESULT {self.test}: {res}")
        return res


def banner(title):
    log("=" * 70)
    log(title)
    log("=" * 70)


# --------------------------------------------------------------------------- preflight
def cmd_preflight(a):
    banner("PREFLIGHT (read-only)")
    rep = Report("preflight")
    r = http("GET", a.base_url + "/", timeout=15)
    rep.check("P1", "AI endpoint reachable through the load balancer",
              "PASS" if r["status"] == 200 else "FAIL", f"GET {a.base_url}/ -> HTTP {r['status']} {r['error'] or ''}".strip())

    r = http("GET", a.base_url + "/api/tags", timeout=20)
    names = []
    if r["status"] == 200:
        try:
            names = [m.get("name", "") for m in json.loads(r["body"]).get("models", [])]
        except ValueError:
            pass
    rep.check("P2", f"Model '{a.model}' is staged", "PASS" if a.model in names else "FAIL",
              f"models listed: {names}")

    if have_docker():
        reps = discover_replicas(a)
        rep.check("P3", "Two inference replicas are running", "PASS" if len(reps) >= 2 else "FAIL",
                  f"found {len(reps)}: {reps}")
        s, h = inspect_state(a.lb_container)
        rep.check("P4", f"Load balancer container '{a.lb_container}' is running",
                  "PASS" if s == "running" else "FAIL", f"state={s} health={h}")
    else:
        rep.check("P3", "Two inference replicas are running", "SKIP", "docker CLI not available on this host")
        rep.check("P4", "Load balancer container is running", "SKIP", "docker CLI not available on this host")

    log("Sending one small generation request ...")
    g = generate(a, num_predict=8, timeout=a.timeout)
    log(req_line("probe", g))
    rep.check("P5", "A real inference request succeeds end-to-end", "PASS" if g["ok"] else "FAIL",
              f"HTTP {g['status']} in {g['latency']:.2f}s {g['error'] or ''}".strip())
    return rep.save(a.outdir)


# --------------------------------------------------------------------------- load test
class DockerSampler(threading.Thread):
    """Samples `docker stats` for the given containers until stopped."""

    def __init__(self, names, interval=2.0):
        super().__init__(daemon=True)
        self.names, self.interval, self.samples = names, interval, []
        self.stop_evt = threading.Event()

    def run(self):
        while not self.stop_evt.is_set():
            rc, out, _ = sh(["docker", "stats", "--no-stream", "--format", "{{.Name}}|{{.CPUPerc}}|{{.MemPerc}}"]
                            + self.names, timeout=30, quiet=True)
            if rc == 0:
                for line in out.splitlines():
                    parts = line.split("|")
                    if len(parts) == 3:
                        try:
                            self.samples.append(dict(name=parts[0], cpu=float(parts[1].strip("%")),
                                                     mem=float(parts[2].strip("%"))))
                        except ValueError:
                            pass
            self.stop_evt.wait(self.interval)

    def stop(self):
        self.stop_evt.set()
        self.join(timeout=35)


def level_stats(c, results, wall, samples, replicas, a):
    ok = [r for r in results if r["ok"]]
    n = len(results)
    lat = [r["latency"] for r in ok]
    tps = [r["tps"] for r in ok if r["tps"] > 0]
    tokens = sum(r["eval_count"] for r in ok)
    d = dict(
        concurrency=c, requests=n, ok=len(ok), errors=n - len(ok),
        rate_limited_429=sum(1 for r in results if r["status"] == 429),
        error_rate_pct=round(100.0 * (n - len(ok)) / n, 2) if n else 100.0,
        p50_s=pctl(lat, 50), p95_s=pctl(lat, 95), max_s=max(lat) if lat else float("nan"),
        mean_s=mean(lat), tps_per_request_mean=mean(tps), tps_aggregate=tokens / wall if wall else 0.0,
        throughput_rps=len(ok) / wall if wall else 0.0, wall_s=wall, tokens_total=tokens,
        replica_cpu_avg_pct_of_limit=None, replica_cpu_peak_pct_of_limit=None,
        replica_mem_peak_pct=None, lb_cpu_peak_pct_of_limit=None,
    )
    rs = [s for s in samples if s["name"] in replicas]
    if rs:
        d["replica_cpu_avg_pct_of_limit"] = mean([s["cpu"] / a.cpu_limit for s in rs])
        d["replica_cpu_peak_pct_of_limit"] = max(s["cpu"] / a.cpu_limit for s in rs)
        d["replica_mem_peak_pct"] = max(s["mem"] for s in rs)
    ls = [s for s in samples if s["name"] == a.lb_container]
    if ls:
        d["lb_cpu_peak_pct_of_limit"] = max(s["cpu"] / a.lb_cpu_limit for s in ls)
    return d


def analyze_bottleneck(rows, a, have_stats):
    """Returns (verdict, evidence[]). A heuristic - the evidence is what matters."""
    ev, base, top = [], rows[0], rows[-1]
    mid = next((r for r in rows if r["concurrency"] <= a.slots and r["concurrency"] > 1), rows[0])

    if base["tps_per_request_mean"] and top["tps_per_request_mean"]:
        drop = 100 * (1 - top["tps_per_request_mean"] / base["tps_per_request_mean"])
        ev.append(f"Per-request speed went {fmt(base['tps_per_request_mean'], 1)} -> "
                  f"{fmt(top['tps_per_request_mean'], 1)} tok/s from c={base['concurrency']} to c={top['concurrency']} "
                  f"({-drop:+.0f}% change in per-request generation speed)")
    else:
        drop = 0
    if base["tps_aggregate"]:
        scale = top["tps_aggregate"] / base["tps_aggregate"]
        ev.append(f"Aggregate throughput scaled {scale:.2f}x ({fmt(base['tps_aggregate'], 1)} -> "
                  f"{fmt(top['tps_aggregate'], 1)} tok/s) for {top['concurrency'] / base['concurrency']:.0f}x the users "
                  f"(inference slots available: {a.slots})")
    if mid["p50_s"] and top["p50_s"] == top["p50_s"]:
        growth = top["p50_s"] / mid["p50_s"]
        ev.append(f"p50 latency grew {growth:.2f}x from c={mid['concurrency']} to c={top['concurrency']} "
                  f"({fmt(mid['p50_s'])}s -> {fmt(top['p50_s'])}s)")
    else:
        growth = 1
    cpu_peak = max([r["replica_cpu_peak_pct_of_limit"] or 0 for r in rows])
    cpu_avg_top = top["replica_cpu_avg_pct_of_limit"] or 0
    mem_peak = max([r["replica_mem_peak_pct"] or 0 for r in rows])
    lb_peak = max([r["lb_cpu_peak_pct_of_limit"] or 0 for r in rows])
    if have_stats:
        ev.append(f"Replica CPU at c={top['concurrency']}: avg {cpu_avg_top:.0f}% / peak {cpu_peak:.0f}% of the "
                  f"{a.cpu_limit} CPU limit; peak memory {mem_peak:.0f}% of limit; nginx peak {lb_peak:.0f}% of its CPU limit")
    else:
        ev.append("docker stats unavailable - CPU/memory evidence missing; verdict is inferred from latency/tps only")

    n429 = sum(r["rate_limited_429"] for r in rows)
    errs = sum(r["errors"] for r in rows)
    if n429:
        verdict = "nginx rate limiter (HTTP 429s during the load test)"
        ev.append(f"{n429} request(s) were rate limited - lower concurrency/pacing or raise limit_req for the load test")
    elif errs:
        verdict = "inference backend saturation (errors/timeouts under load)"
        ev.append(f"{errs} failed request(s) - see per-request lines in the log")
    elif have_stats and mem_peak >= 90:
        verdict = "replica memory limit"
    elif have_stats and (cpu_avg_top >= 80 or cpu_peak >= 90):
        verdict = f"CPU - inference is compute-bound on the {a.cpu_limit}-CPU replica quota"
    elif top["concurrency"] > a.slots and growth >= 1.5:
        verdict = (f"inference slot queueing (2 replicas x OLLAMA_NUM_PARALLEL = {a.slots} concurrent slots; "
                   f"requests beyond that wait)")
    elif not have_stats and drop >= 25:
        verdict = "CPU contention between concurrent generations (inferred, no docker stats)"
    else:
        verdict = f"none reached up to c={top['concurrency']} - try higher concurrency"
    return verdict, ev


def make_load_chart(rows, path, verdict, have_stats):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False
    xs = [r["concurrency"] for r in rows]
    fig, axs = plt.subplots(2, 2, figsize=(11, 8))
    ax = axs[0][0]
    ax.plot(xs, [r["p50_s"] for r in rows], "o-", label="p50")
    ax.plot(xs, [r["p95_s"] for r in rows], "s--", label="p95")
    ax.set(title="Latency vs concurrency", xlabel="concurrent users", ylabel="seconds", xticks=xs)
    ax.legend()
    ax.grid(alpha=.3)
    ax = axs[0][1]
    ax.plot(xs, [r["tps_per_request_mean"] for r in rows], "o-", label="per request (mean)")
    ax.plot(xs, [r["tps_aggregate"] for r in rows], "s--", label="aggregate (all users)")
    ax.set(title="Tokens per second", xlabel="concurrent users", ylabel="tokens/s", xticks=xs)
    ax.legend()
    ax.grid(alpha=.3)
    ax = axs[1][0]
    bars = ax.bar([str(x) for x in xs], [r["error_rate_pct"] for r in rows], color="#c0392b")
    for b, r in zip(bars, rows):
        ax.annotate(f"{r['error_rate_pct']}%  ({r['errors']}/{r['requests']} failed, {r['rate_limited_429']} x 429)",
                    (b.get_x() + b.get_width() / 2, b.get_height()), ha="center", va="bottom", fontsize=8,
                    xytext=(0, 3), textcoords="offset points")
    ax.set(title="Error rate (incl. HTTP 429)", xlabel="concurrent users", ylabel="% of requests")
    ax.set_ylim(0, max(5, max(r["error_rate_pct"] for r in rows) * 1.2))
    ax.grid(alpha=.3, axis="y")
    ax = axs[1][1]
    if have_stats:
        ax.plot(xs, [r["replica_cpu_avg_pct_of_limit"] for r in rows], "o-", label="replica CPU avg")
        ax.plot(xs, [r["replica_cpu_peak_pct_of_limit"] for r in rows], "s--", label="replica CPU peak")
        ax.plot(xs, [r["replica_mem_peak_pct"] for r in rows], "^:", label="replica memory peak")
        ax.axhline(100, color="grey", lw=.8)
        ax.set(title="Resource use (% of container limit)", xlabel="concurrent users", xticks=xs)
        ax.legend()
        ax.grid(alpha=.3)
    else:
        ax.text(.5, .5, "docker stats not available", ha="center", va="center")
        ax.set_axis_off()
    fig.suptitle(f"AI tier load test - likely bottleneck: {verdict}", fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return True


def cmd_load(a):
    banner("LOAD TEST")
    rep = Report("load")
    levels = [int(x) for x in a.levels.split(",")]
    docker = have_docker()
    replicas = discover_replicas(a) if docker else []
    watch = (replicas + [a.lb_container]) if docker and replicas else []
    log(f"endpoint={a.base_url} model={a.model} levels={levels} requests/level={a.requests} "
        f"num_predict={a.num_predict} replicas={replicas or 'unknown'}")

    log("Warm-up (4 concurrent tiny requests so both replicas have the model loaded; excluded from results)")
    warm, _ = run_pool(a, 4, 4, num_predict=8, tag="warmup")
    if not any(r["ok"] for r in warm):
        rep.check("L0", "Warm-up requests succeed", "FAIL", "no warm-up request succeeded; aborting load test")
        return rep.save(a.outdir)

    rows = []
    for c in levels:
        log("-" * 70)
        log(f"Level: {c} concurrent user(s), {a.requests} requests total")
        sampler = None
        if watch:
            log("$ docker stats --no-stream --format '{{.Name}}|{{.CPUPerc}}|{{.MemPerc}}' "
                + " ".join(watch) + "   # sampled every ~2s during this level")
            CMD_LOG.append(f"{utcnow()}  $ docker stats --no-stream ... {' '.join(watch)}  (sampled during c={c})")
            sampler = DockerSampler(watch)
            sampler.start()
        results, wall = run_pool(a, c, a.requests, tag="load")
        samples = []
        if sampler:
            sampler.stop()
            samples = sampler.samples
        st = level_stats(c, results, wall, samples, replicas, a)
        rows.append(st)
        log(f"  c={c}: ok={st['ok']}/{st['requests']} err={st['error_rate_pct']}% (429s={st['rate_limited_429']}) "
            f"p50={fmt(st['p50_s'])}s p95={fmt(st['p95_s'])}s tps/req={fmt(st['tps_per_request_mean'], 1)} "
            f"tps agg={fmt(st['tps_aggregate'], 1)} wall={wall:.1f}s")
        time.sleep(a.level_pause)  # let the nginx limiter bucket and CPU settle between levels

    have_stats = all(r["replica_cpu_avg_pct_of_limit"] is not None for r in rows)
    verdict, evidence = analyze_bottleneck(rows, a, have_stats)

    os.makedirs(a.outdir, exist_ok=True)
    csv_path = os.path.join(a.outdir, "load_results.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if v is None or (isinstance(v, float) and math.isnan(v)) else
                            (round(v, 3) if isinstance(v, float) else v)) for k, v in r.items()})
    chart_ok = make_load_chart(rows, os.path.join(a.outdir, "load_chart.png"), verdict, have_stats)

    log("")
    log(f"{'users':>5} {'reqs':>5} {'err%':>6} {'p50(s)':>8} {'p95(s)':>8} {'tok/s/req':>10} {'tok/s agg':>10} {'rps':>6}")
    for r in rows:
        log(f"{r['concurrency']:>5} {r['requests']:>5} {r['error_rate_pct']:>6} {fmt(r['p50_s']):>8} "
            f"{fmt(r['p95_s']):>8} {fmt(r['tps_per_request_mean'], 1):>10} {fmt(r['tps_aggregate'], 1):>10} "
            f"{fmt(r['throughput_rps'], 2):>6}")
    log("")
    log(f"LIKELY BOTTLENECK: {verdict}")
    for e in evidence:
        log(f"  - {e}")
    log("")

    rep.metrics = dict(levels=rows, bottleneck=verdict, evidence=evidence, csv="load_results.csv",
                       chart="load_chart.png" if chart_ok else None, params=dict(
                           requests_per_level=a.requests, num_predict=a.num_predict, model=a.model))
    rep.check("L1", "Every concurrency level completed with at least one successful request",
              "PASS" if all(r["ok"] > 0 for r in rows) else "FAIL",
              ", ".join(f"c={r['concurrency']}: {r['ok']}/{r['requests']} ok" for r in rows))
    bad = [r for r in rows if r["error_rate_pct"] > a.max_error_pct]
    rep.check("L2", f"Error rate <= {a.max_error_pct}% at every level", "FAIL" if bad else "PASS",
              ", ".join(f"c={r['concurrency']}: {r['error_rate_pct']}%" for r in rows))
    have_metrics = all(r["p50_s"] == r["p50_s"] and r["tps_per_request_mean"] == r["tps_per_request_mean"] for r in rows)
    rep.check("L3", "Latency (p50/p95), tokens/sec and error rate recorded for each level (CSV)",
              "PASS" if have_metrics else "FAIL", csv_path)
    rep.check("L4", "Results charted", "PASS" if chart_ok else "SKIP",
              "load_chart.png" if chart_ok else "matplotlib not installed (pip install matplotlib); CSV still written")
    rep.check("L5", "Bottleneck identified from the data", "PASS" if have_metrics else "FAIL", verdict)

    if docker and replicas:
        states = {n: inspect_state(n) for n in replicas + [a.lb_container]}
        down = [n for n, (s, _) in states.items() if s != "running"]
        unhealthy = [n for n, (_, h) in states.items() if h == "unhealthy"]
        if down:
            rep.check("L6", "Containers still running after the load test", "FAIL", f"not running: {down}")
        elif unhealthy:
            rep.check("L6", "Containers still running and healthy after the load test", "WARN",
                      f"running but unhealthy: {unhealthy} (the compose healthcheck runs a real generation and can "
                      f"time out under load)")
        else:
            rep.check("L6", "Containers still running and healthy after the load test", "PASS", str(states))
    else:
        rep.check("L6", "Containers still running after the load test", "SKIP", "docker CLI not available")
    return rep.save(a.outdir)


# --------------------------------------------------------------------------- rate-limit test
def cmd_ratelimit(a):
    banner("RATE-LIMIT TEST")
    rep = Report("ratelimit")
    docker = have_docker()
    replicas = discover_replicas(a) if docker else []
    watch = replicas + ([a.lb_container] if docker else [])
    restarts_before = {n: restart_count(n) for n in watch} if docker else {}

    log(f"Waiting {a.cooldown}s so the limiter bucket starts empty")
    time.sleep(a.cooldown)
    n = a.burst
    log(f"Releasing {n} simultaneous POST /api/generate requests (num_predict={a.rl_num_predict})")
    barrier, results, lock = threading.Barrier(n), [], threading.Lock()

    def worker(i):
        barrier.wait()
        r = generate(a, num_predict=a.rl_num_predict)
        r["idx"] = i
        with lock:
            results.append(r)

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    codes = Counter(r["status"] for r in results)
    n429 = codes.get(429, 0)
    n200 = codes.get(200, 0)
    n5xx = sum(v for k, v in codes.items() if 500 <= k < 600)
    nconn = codes.get(0, 0)
    accepted = sum(v for k, v in codes.items() if k not in (0, 429))  # got an HTTP answer that wasn't 429
    window = max(r["t_start"] for r in results) - min(r["t_start"] for r in results)
    log("Status codes: " + ", ".join(f"{k}={v}" for k, v in sorted(codes.items())) +
        f"   (send window {window:.2f}s)")
    for r in sorted(results, key=lambda x: x["t_start"])[:5]:
        log(req_line("first-few", r))
    servers = {r["headers"].get("Server", "?") for r in results if r["status"] == 429}
    bound = a.rl_burst + 1 + math.ceil(a.rl_rate * window) + 2
    rep.metrics = dict(burst=n, status_codes={str(k): v for k, v in codes.items()}, send_window_s=window,
                       accepted=accepted, accepted_upper_bound=bound, server_header_on_429=sorted(servers))

    rep.check("R1", "HTTP 429 returned during the burst", "PASS" if n429 > 0 else "FAIL",
              f"{n429} of {n} requests got 429 (Server header on 429s: {sorted(servers) or 'n/a'})")
    rep.check("R2", "Limiter admits only about burst + rate x window requests",
              "PASS" if accepted <= bound else "FAIL",
              f"accepted={accepted}, expected <= {bound} (burst {a.rl_burst} + 1 + {a.rl_rate}/s x {window:.2f}s + 2 slack)")
    rep.check("R3", "Accepted requests were served with no 5xx / connection errors",
              "PASS" if (n200 >= 1 and n5xx == 0 and nconn == 0) else "FAIL",
              f"200={n200}, 5xx={n5xx}, connection errors/timeouts={nconn}")

    log(f"Waiting {a.cooldown}s for the limiter to refill, then sending a normal request")
    time.sleep(a.cooldown)
    g = generate(a, num_predict=8)
    log(req_line("post-burst", g))
    rep.check("R4", "Service answers a normal request after the burst", "PASS" if g["ok"] else "FAIL",
              f"HTTP {g['status']} in {g['latency']:.2f}s {g['error'] or ''}".strip())

    if docker and watch:
        probs, warns = [], []
        for name in watch:
            s, h = inspect_state(name)
            rc_now = restart_count(name)
            if s != "running":
                probs.append(f"{name} state={s}")
            if restarts_before.get(name) is not None and rc_now is not None and rc_now > restarts_before[name]:
                probs.append(f"{name} restarted during the burst")
            if h == "unhealthy":
                warns.append(f"{name} unhealthy")
        rep.check("R5", "All AI containers still running (no restarts) after the burst",
                  "FAIL" if probs else ("WARN" if warns else "PASS"),
                  "; ".join(probs + warns) or f"checked {watch}")
    else:
        rep.check("R5", "All AI containers still running after the burst", "SKIP", "docker CLI not available")
    return rep.save(a.outdir)


# --------------------------------------------------------------------------- failover test
def make_failover_chart(recs, kill_ts, recovered_ts, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False
    fig, ax = plt.subplots(figsize=(10, 4.5))
    for ok, color, label in ((True, "#2e8b57", "success"), (False, "#c0392b", "failed")):
        pts = [(r["t_start"] - kill_ts, r["latency"]) for r in recs if r["ok"] == ok]
        if pts:
            ax.scatter(*zip(*pts), c=color, label=label, s=22)
    ax.axvline(0, color="black", ls="--", label="docker kill")
    if recovered_ts:
        ax.axvline(recovered_ts - kill_ts, color="#1f77b4", ls=":", label="replica healthy again")
    ax.set(title="Failover: request latency around the kill", xlabel="seconds since kill (request start)",
           ylabel="latency (s)")
    ax.legend()
    ax.grid(alpha=.3)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return True


def cmd_failover(a):
    if not a.confirm_disruptive:
        print("REFUSING: the failover test kills an inference container. It only runs when a human explicitly "
              "asks for it (tests/run_tests.sh failover, which passes --confirm-disruptive).", file=sys.stderr)
        sys.exit(2)
    banner("FAILOVER TEST (DISRUPTIVE: kills one inference container)")
    rep = Report("failover")
    if not have_docker():
        rep.check("F1", "docker CLI available", "FAIL", "cannot kill/inspect containers without docker")
        return rep.save(a.outdir)
    replicas = discover_replicas(a)
    target = a.target or (replicas[0] if replicas else None)
    if len(replicas) < 2 or target not in replicas:
        rep.check("F1", "Two inference replicas running and target is one of them", "FAIL",
                  f"replicas={replicas} target={target}. Refusing to kill anything that is not an "
                  f"'{a.service}' compose replica.")
        return rep.save(a.outdir)
    rep.check("F1", "Two inference replicas running and target is one of them", "PASS",
              f"replicas={replicas}; will kill: {target}")

    log("Warm-up: loading the model on both replicas")
    run_pool(a, 4, 4, num_predict=8, tag="warmup")

    stop, lock, records = threading.Event(), threading.Lock(), []
    t_zero = time.time()

    def user(i):
        while not stop.is_set():
            r = generate(a, num_predict=a.fo_num_predict, timeout=a.fo_req_timeout)
            r["user"] = i
            with lock:
                records.append(r)
            log(req_line(f"t+{r['t_start'] - t_zero:6.1f}s u{i}", r))
            stop.wait(a.think)

    threads = [threading.Thread(target=user, args=(i,), daemon=True) for i in range(a.users)]
    log(f"Starting {a.users} steady users (paced, well below the nginx rate limit)")
    for t in threads:
        t.start()
    log(f"Baseline traffic for {a.baseline}s ...")
    time.sleep(a.baseline)

    with lock:
        base = list(records)
    base_ok = sum(1 for r in base if r["ok"])
    base_pass = len(base) >= 3 and base_ok / len(base) >= 0.9
    rep.check("F2", "Baseline: requests succeed before the failure", "PASS" if base_pass else "FAIL",
              f"{base_ok}/{len(base)} succeeded")
    if not base_pass:
        stop.set()
        log("Baseline is not healthy - NOT killing anything.")
        return rep.save(a.outdir)

    restarts_before = restart_count(target)
    kill_ts = time.time()
    rc, out, err = sh(["docker", "kill", target])
    rep.check("F3", f"Inference replica '{target}' killed during active requests", "PASS" if rc == 0 else "FAIL",
              (out or err) + f" (RestartCount before: {restarts_before})")
    if rc != 0:
        stop.set()
        return rep.save(a.outdir)

    log("Watching the replica (docker inspect every {}s) while traffic continues ...".format(a.poll))
    last, detected, recovered_at, timeline = None, None, None, []
    deadline = kill_ts + a.recovery_timeout
    while time.time() < deadline:
        s, h = inspect_state(target)
        now = time.time()
        if (s, h) != last:
            log(f"  {target}: state={s} health={h}  (t+{now - kill_ts:.1f}s after kill)")
            timeline.append(dict(t=round(now - kill_ts, 1), state=s, health=h))
            last = (s, h)
        if detected is None and (s != "running" or h != "healthy"):
            detected = now - kill_ts
        if detected is not None and s == "running" and h == "healthy":
            recovered_at = now
            break
        time.sleep(a.poll)

    if recovered_at:
        log(f"Replica healthy again after {recovered_at - kill_ts:.1f}s. Keeping traffic on for {a.post_window}s more ...")
        time.sleep(a.post_window)
    else:
        log(f"Replica did NOT become healthy within {a.recovery_timeout}s.")
    stop.set()
    for t in threads:
        t.join(timeout=a.fo_req_timeout + 5)

    with lock:
        recs = sorted(records, key=lambda r: r["t_start"])
    pre = [r for r in recs if r["t_end"] < kill_ts]
    inflight = [r for r in recs if r["t_start"] < kill_ts <= r["t_end"]]
    post = [r for r in recs if r["t_start"] >= kill_ts]

    def summ(name, rs):
        ok = [r for r in rs if r["ok"]]
        lat = [r["latency"] for r in ok]
        log(f"  {name:<22} n={len(rs):<3} ok={len(ok):<3} failed={len(rs) - len(ok):<3} "
            f"p50={fmt(pctl(lat, 50))}s p95={fmt(pctl(lat, 95))}s max={fmt(max(lat) if lat else None)}s")

    log("")
    log("Request classification relative to the kill:")
    summ("before kill", pre)
    summ("in flight at kill", inflight)
    summ("started after kill", post)
    log("")

    os.makedirs(a.outdir, exist_ok=True)
    with open(os.path.join(a.outdir, "failover_requests.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["seconds_since_kill", "latency_s", "http_status", "ok", "phase", "error"])
        for r in recs:
            phase = "pre" if r in pre else ("in_flight" if r in inflight else "post")
            w.writerow([round(r["t_start"] - kill_ts, 2), round(r["latency"], 3), r["status"], r["ok"], phase, r["error"] or ""])
    chart_ok = make_failover_chart(recs, kill_ts, recovered_at, os.path.join(a.outdir, "failover_timeline.png"))

    post_ok = [r for r in post if r["ok"]]
    post_rate = 100.0 * len(post_ok) / len(post) if post else 0.0
    fails = [f"{r['status']} {r['error']}" for r in post if not r["ok"]][:5]
    rep.check("F4", f"Requests started after the kill keep succeeding via the remaining replica (>= {a.min_success_pct}%)",
              "PASS" if (len(post) >= 5 and post_rate >= a.min_success_pct) else "FAIL",
              f"{len(post_ok)}/{len(post)} = {post_rate:.1f}% ok; max latency "
              f"{fmt(max((r['latency'] for r in post_ok), default=None))}s; failures: {fails or 'none'}")
    in_bad = [r for r in inflight if not r["ok"]]
    rep.check("F5", "Requests that were mid-flight on the killed replica",
              "WARN" if in_bad else "PASS",
              f"{len(in_bad)} of {len(inflight)} in-flight requests failed. "
              + ("nginx does not retry a POST that was already sent to an upstream unless "
                 "'non_idempotent' is added to proxy_next_upstream; this is the expected loss window."
                 if in_bad else "none lost"))

    states_txt = " -> ".join(f"{t['state']}/{t['health']}@{t['t']}s" for t in timeline)
    rep.check("F6", "Failure detected: replica left running/healthy (Docker state + healthcheck)",
              "PASS" if detected is not None else "FAIL",
              f"detected at t+{fmt(detected, 1)}s; timeline: {states_txt}")

    lines = docker_logs(a.lb_container, kill_ts)
    if lines is None:
        rep.check("F7", "Load balancer stops routing to the failed replica (nginx passive health check)", "SKIP",
                  "could not read ai-lb logs")
    else:
        lb_err = [l for l in lines if "[error]" in l and "upstream" in l]
        down_for = (recovered_at - kill_ts) if recovered_at else a.recovery_timeout
        bound = a.nginx_max_fails + math.ceil(down_for / a.nginx_fail_timeout) + 2
        rep.check("F7", "Load balancer stops routing to the failed replica (nginx passive health check)",
                  "PASS" if len(lb_err) <= bound else "FAIL",
                  f"{len(lb_err)} upstream error log lines since the kill; bound {bound} "
                  f"(max_fails {a.nginx_max_fails} + 1 probe per {a.nginx_fail_timeout}s fail_timeout over {down_for:.0f}s + 2). "
                  f"Without removal, about half of all requests would log an error. Sample: "
                  f"{(lb_err[0][:160] if lb_err else 'none')}")

    rc_after = restart_count(target)
    auto = recovered_at is not None
    if auto and restarts_before is not None and rc_after is not None and rc_after > restarts_before:
        rep.check("F8", "Replica recovered automatically (Docker restart policy, no manual action)", "PASS",
                  f"healthy again at t+{recovered_at - kill_ts:.1f}s; RestartCount {restarts_before} -> {rc_after}")
    elif auto:
        rep.check("F8", "Replica recovered automatically (Docker restart policy, no manual action)", "WARN",
                  f"healthy at t+{recovered_at - kill_ts:.1f}s but RestartCount did not increase ({restarts_before} -> {rc_after})")
    else:
        rep.check("F8", "Replica recovered automatically (Docker restart policy, no manual action)", "FAIL",
                  f"not healthy within {a.recovery_timeout}s. Last state: {last}. "
                  f"Review, then restore manually with: docker start {target}")

    if auto:
        tl = docker_logs(target, kill_ts) or []
        gin = [l for l in tl if "POST" in l and "/api/generate" in l]
        rep.check("F9", "Recovered replica serves client traffic again through the load balancer",
                  "PASS" if gin else "FAIL",
                  f"{len(gin)} /api/generate requests served by {target} since the kill" +
                  ("" if gin else ". nginx resolves upstream hostnames only at startup; if the container came back "
                                  "with a new IP, nginx keeps sending to the old one (see README: known risks)."))
    else:
        rep.check("F9", "Recovered replica serves client traffic again", "SKIP", "replica did not recover")

    final = [inspect_state(n) for n in replicas]
    tail = [r for r in recs if r["t_start"] >= (recovered_at or kill_ts) + 5][-3:]
    end_ok = all(s == "running" and h in ("healthy", "none") for s, h in final) and bool(tail) and all(r["ok"] for r in tail)
    rep.check("F10", "End state: both replicas running and healthy, last requests succeed", "PASS" if end_ok else "FAIL",
              f"states={dict(zip(replicas, final))}; last {len(tail)} requests ok={[r['ok'] for r in tail]}")

    rep.metrics = dict(target=target, kill_time_utc=datetime.fromtimestamp(kill_ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                       detected_after_s=detected, recovered_after_s=(recovered_at - kill_ts) if recovered_at else None,
                       counts=dict(pre=len(pre), in_flight=len(inflight), post=len(post)),
                       post_success_pct=post_rate, timeline=timeline, csv="failover_requests.csv",
                       chart="failover_timeline.png" if chart_ok else None)
    return rep.save(a.outdir)


# --------------------------------------------------------------------------- report
def read_text(path, limit=None):
    try:
        with open(path, errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return ""
    if limit and len(lines) > limit:
        half = limit // 2
        lines = lines[:half] + [f"... ({len(lines) - limit} lines omitted - see the .log file) ..."] + lines[-half:]
    return "\n".join(lines)


def cmd_report(a):
    out, tests = a.outdir, a.tests.split()
    icon = {"PASS": "PASS", "FAIL": "**FAIL**", "WARN": "WARN", "SKIP": "SKIP"}
    L = [f"# AI tier test run - {os.path.basename(out.rstrip('/'))}", ""]
    L += ["| Field | Value |", "|---|---|",
          f"| Started (UTC) | {a.started} |", f"| Ended (UTC) | {a.ended} |",
          f"| Tests run | {', '.join(tests)} |",
          f"| AI tool used to build the tests | {os.environ.get('AI_TOOL', 'unspecified')} "
          f"({os.environ.get('AI_MODEL', 'unspecified')}) |",
          f"| Who/what executed the tests | {os.environ.get('RUN_BY', 'unspecified')} |",
          f"| Prompt(s) | see `{os.environ.get('PROMPTS_FILE', 'tests/PROMPTS.md')}` (copied below) |",
          f"| Git commit | {a.commit or 'n/a'} |", f"| Endpoint | {a.base_url} |", ""]

    results = {}
    for t in tests:
        p = os.path.join(out, f"{t}.json")
        if os.path.exists(p):
            with open(p) as f:
                results[t] = json.load(f)

    L += ["## Summary", "", "| Test | Result | PASS | FAIL | WARN | SKIP |", "|---|---|---|---|---|---|"]
    for t in tests:
        r = results.get(t)
        if not r:
            L.append(f"| {t} | **NOT RUN / CRASHED** | | | | |")
            continue
        c = Counter(x["status"] for x in r["checks"])
        L.append(f"| {t} | {icon[r['result']]} | {c['PASS']} | {c['FAIL']} | {c['WARN']} | {c['SKIP']} |")
    L.append("")

    for t in tests:
        r = results.get(t)
        if not r:
            continue
        L += [f"## {t}", "", f"Started {r['started']} - ended {r['ended']} - result **{r['result']}**", "",
              "| ID | Check | Result | Detail |", "|---|---|---|---|"]
        for x in r["checks"]:
            L.append(f"| {x['id']} | {x['description']} | {icon[x['status']]} | {x['detail'].replace('|', '/').replace(chr(10), ' ')} |")
        L.append("")
        m = r.get("metrics", {})
        if t == "load" and m.get("levels"):
            L += ["| Users | Requests | Error % | p50 (s) | p95 (s) | tok/s per request | tok/s aggregate | req/s |",
                  "|---|---|---|---|---|---|---|---|"]
            for v in m["levels"]:
                L.append(f"| {v['concurrency']} | {v['requests']} | {v['error_rate_pct']} | {fmt(v['p50_s'])} | "
                         f"{fmt(v['p95_s'])} | {fmt(v['tps_per_request_mean'], 1)} | {fmt(v['tps_aggregate'], 1)} | "
                         f"{fmt(v['throughput_rps'])} |")
            L += ["", f"**Likely bottleneck:** {m['bottleneck']}", ""] + [f"- {e}" for e in m["evidence"]] + [""]
            if m.get("chart"):
                L += [f"![load test chart]({m['chart']})", ""]
        if t == "failover" and m.get("chart"):
            L += [f"![failover timeline]({m['chart']})", ""]

    L += ["## Commands run", "", "```"]
    L.append(read_text(os.path.join(out, "commands.txt")))
    for t in tests:
        for c in results.get(t, {}).get("commands", []):
            L.append(f"[{t}] {c}")
    L += ["```", ""]

    L += ["## Environment", "", "```", read_text(os.path.join(out, "environment.txt")), "```", ""]
    L += ["## Prompt(s) used to generate these tests", "", "<details><summary>Show prompts</summary>", "",
          read_text(os.environ.get("PROMPTS_FILE", ""), None) or "(prompts file not found)", "", "</details>", ""]
    L += ["## Raw output", ""]
    for t in tests:
        txt = read_text(os.path.join(out, f"{t}.log"), limit=400)
        if txt:
            L += [f"<details><summary>{t}.log</summary>", "", "```", txt, "```", "", "</details>", ""]
    with open(os.path.join(out, "run_record.md"), "w") as f:
        f.write("\n".join(L) + "\n")
    print(f"wrote {os.path.join(out, 'run_record.md')}")
    return 0


# --------------------------------------------------------------------------- CLI
def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--base-url", default=env("AI_BASE_URL", "http://127.0.0.1:11434"))
    common.add_argument("--model", default=env("CHAT_MODEL", "qwen2.5:1.5b"))
    common.add_argument("--outdir", default=os.path.join("tests", "results", "adhoc-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")))
    common.add_argument("--timeout", type=float, default=env("REQ_TIMEOUT", 180.0, float), help="per-request timeout (s)")
    common.add_argument("--prompt", default=env("TEST_PROMPT", DEFAULT_PROMPT))
    common.add_argument("--num-predict", type=int, default=env("NUM_PREDICT", 64, int))
    common.add_argument("--service", default=env("OLLAMA_SERVICE", "ollama"), help="compose service name of the replicas")
    common.add_argument("--lb-container", default=env("LB_CONTAINER", "ai-lb"))

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("preflight", parents=[common])

    s = sub.add_parser("load", parents=[common])
    s.add_argument("--levels", default=env("LOAD_LEVELS", "1,5,10"))
    s.add_argument("--requests", type=int, default=env("LOAD_REQUESTS", 20, int), help="requests per level")
    s.add_argument("--max-error-pct", type=float, default=env("MAX_ERROR_PCT", 5.0, float))
    s.add_argument("--slots", type=int, default=env("INFERENCE_SLOTS", 4, int), help="replicas x OLLAMA_NUM_PARALLEL")
    s.add_argument("--cpu-limit", type=float, default=env("REPLICA_CPU_LIMIT", 2.0, float))
    s.add_argument("--lb-cpu-limit", type=float, default=env("LB_CPU_LIMIT", 0.5, float))
    s.add_argument("--level-pause", type=float, default=env("LEVEL_PAUSE", 5.0, float))

    s = sub.add_parser("ratelimit", parents=[common])
    s.add_argument("--burst", type=int, default=env("RL_BURST_REQUESTS", 60, int))
    s.add_argument("--rl-rate", type=float, default=env("RL_RATE", 5.0, float), help="nginx limit_req rate (r/s)")
    s.add_argument("--rl-burst", type=int, default=env("RL_BURST", 10, int), help="nginx limit_req burst")
    s.add_argument("--rl-num-predict", type=int, default=4)
    s.add_argument("--cooldown", type=float, default=env("RL_COOLDOWN", 4.0, float))

    s = sub.add_parser("failover", parents=[common])
    s.add_argument("--confirm-disruptive", action="store_true", help="REQUIRED: acknowledges a container will be killed")
    s.add_argument("--target", default=env("FAILOVER_TARGET", ""), help="replica container to kill (default: first)")
    s.add_argument("--users", type=int, default=env("FO_USERS", 3, int))
    s.add_argument("--think", type=float, default=0.5, help="pause between a user's requests (s)")
    s.add_argument("--baseline", type=float, default=env("FO_BASELINE", 20.0, float))
    s.add_argument("--post-window", type=float, default=env("FO_POST_WINDOW", 30.0, float))
    s.add_argument("--recovery-timeout", type=float, default=env("FO_RECOVERY_TIMEOUT", 240.0, float))
    s.add_argument("--poll", type=float, default=1.0)
    s.add_argument("--min-success-pct", type=float, default=env("FO_MIN_SUCCESS_PCT", 95.0, float))
    s.add_argument("--fo-num-predict", type=int, default=16)
    s.add_argument("--fo-req-timeout", type=float, default=60.0)
    s.add_argument("--nginx-max-fails", type=int, default=2)
    s.add_argument("--nginx-fail-timeout", type=float, default=10.0)

    s = sub.add_parser("report", parents=[common])
    s.add_argument("--started", default="")
    s.add_argument("--ended", default="")
    s.add_argument("--tests", default="")
    s.add_argument("--commit", default="")
    return p


def main():
    a = build_parser().parse_args()
    if a.cmd == "report":
        sys.exit(cmd_report(a))
    res = {"preflight": cmd_preflight, "load": cmd_load, "ratelimit": cmd_ratelimit, "failover": cmd_failover}[a.cmd](a)
    sys.exit(0 if res in ("PASS", "SKIP") else 1)


if __name__ == "__main__":
    main()
