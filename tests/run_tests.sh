#!/usr/bin/env bash
#
# tests/run_tests.sh - run the AI-tier tests and write a documented run record.
#
#   ./tests/run_tests.sh                    safe suite: preflight + load + ratelimit
#   ./tests/run_tests.sh load               just the load test (preflight always runs first)
#   ./tests/run_tests.sh ratelimit
#   ./tests/run_tests.sh failover           DISRUPTIVE: kills one inference container.
#                                           Only run when you explicitly want it.
#   ./tests/run_tests.sh safe failover      safe suite, then failover last
#
# Options
#   --yes     skip the interactive confirmation for failover. HUMANS ONLY - an AI tool
#             must never add this on its own; it has to ask the human first.
#   -h        help
#
# Environment (all optional)
#   AI_TOOL / AI_MODEL   AI tool + model used to build the tests (recorded in the run record)
#   RUN_BY               who executed this run, e.g. "Claude Code executing tests/run_tests.sh"
#   PROMPTS_FILE         file holding the prompts used (default: tests/PROMPTS.md)
#   AI_BASE_URL, CHAT_MODEL, LOAD_REQUESTS, NUM_PREDICT, ...   see tests/README.md
#
# No passwords, keys, tokens or service-account files are read or needed. Only the
# commands that appear in <run dir>/commands.txt and the "$ ..." lines of each log are run.

set -u -o pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
cd "$REPO_ROOT" || exit 1          # work from the repo root so logged paths are relative
SUITE="tests/ai_tests.py"
PY="${PYTHON:-python3}"

export AI_TOOL="${AI_TOOL:-Claude (Anthropic, claude.ai chat)}"
export AI_MODEL="${AI_MODEL:-Claude Sonnet 5.5}"
export RUN_BY="${RUN_BY:-Human running tests/run_tests.sh}"
export PROMPTS_FILE="${PROMPTS_FILE:-tests/PROMPTS.md}"
BASE_URL="${AI_BASE_URL:-http://127.0.0.1:11434}"

usage() { sed -n '3,22p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

ASSUME_YES=0
REQUESTED=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --yes) ASSUME_YES=1 ;;
    safe) REQUESTED+=" load ratelimit" ;;
    preflight|load|ratelimit|failover) REQUESTED+=" $1" ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done
[[ -z "${REQUESTED// /}" ]] && REQUESTED=" load ratelimit"

# Fixed order: preflight (always), load, ratelimit, failover (always last)
ORDER=(preflight)
for t in load ratelimit failover; do
  [[ " $REQUESTED " == *" $t "* ]] && ORDER+=("$t")
done

# ---- safety gate for the disruptive test ------------------------------------------
if [[ " ${ORDER[*]} " == *" failover "* ]]; then
  echo "WARNING: the 'failover' test will KILL one AI inference container (docker kill)"
  echo "         while requests are in flight. It is expected to recover on its own."
  if [[ $ASSUME_YES -ne 1 ]]; then
    if [[ ! -t 0 ]]; then
      echo "Refusing: no interactive terminal. A human must explicitly request this test" >&2
      echo "(re-run with --yes only if you, the human, asked for the failover test)." >&2
      exit 2
    fi
    read -r -p "Type 'kill-replica' to continue: " answer
    [[ "$answer" == "kill-replica" ]] || { echo "Aborted - nothing was touched."; exit 2; }
  fi
fi

# ---- run directory ----------------------------------------------------------------
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="tests/results/$RUN_ID"
mkdir -p "$OUT"
CMDS="$OUT/commands.txt"
START_ISO="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
record() { printf '%s  $ %s\n' "$(date -u +%H:%M:%SZ)" "$*" >> "$CMDS"; }

echo "Run id : $RUN_ID"
echo "Tests  : ${ORDER[*]}"
echo "Output : $OUT"

# ---- environment snapshot (versions + container list only; no env vars, no secrets) -
{
  echo "date_utc:  $START_ISO"
  echo "host:      $(uname -srm)"
  echo "python:    $("$PY" --version 2>&1)"
  echo "docker:    $(docker --version 2>&1)"
  echo "compose:   $(docker compose version 2>&1 | head -1)"
  echo "git:       $(git -C "$HERE" rev-parse --short HEAD 2>/dev/null || echo 'not a git repo')"
  echo "endpoint:  $BASE_URL"
  echo
  docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}' 2>&1
} > "$OUT/environment.txt"
record "uname -srm; $PY --version; docker --version; docker compose version; git rev-parse --short HEAD"
record "docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'"
COMMIT="$(git -C "$HERE" rev-parse --short HEAD 2>/dev/null || true)"

# ---- run ----------------------------------------------------------------------------
run_test() {
  local name="$1"; shift
  record "$PY -u $SUITE $name --outdir $OUT $*"
  "$PY" -u "$SUITE" "$name" --outdir "$OUT" "$@" 2>&1 | tee "$OUT/$name.log"
  return "${PIPESTATUS[0]}"
}

OVERALL=0
for t in "${ORDER[@]}"; do
  echo
  echo "################ $t ################"
  extra=()
  [[ "$t" == "failover" ]] && extra=(--confirm-disruptive)
  run_test "$t" ${extra[@]+"${extra[@]}"}
  rc=$?
  if [[ $rc -ne 0 ]]; then
    OVERALL=1
    if [[ "$t" == "preflight" ]]; then
      echo "Preflight failed - skipping the remaining tests (fix the stack first)."
      break
    fi
  fi
done

END_ISO="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
record "$PY $SUITE report --outdir $OUT"
"$PY" "$SUITE" report --outdir "$OUT" --started "$START_ISO" --ended "$END_ISO" \
  --tests "${ORDER[*]}" --commit "$COMMIT" --base-url "$BASE_URL"

echo
if [[ $OVERALL -eq 0 ]]; then echo "OVERALL: PASS"; else echo "OVERALL: FAIL (see $OUT/run_record.md)"; fi
echo "Run record: $OUT/run_record.md"
echo "Commit it:  git add tests/results/$RUN_ID && git commit -m 'AI tier test run $RUN_ID'"
exit $OVERALL
