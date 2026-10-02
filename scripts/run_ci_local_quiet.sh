#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_ROOT="${AGENT_CI_LOG_DIR:-${TMPDIR:-/tmp}/agent-ci-logs}"
SHARED_ROOT="${AGENT_CI_SHARED_DIR:-${TMPDIR:-/tmp}/agent-ci-shared-$(id -u)}"
mkdir -p "$LOG_ROOT" "$SHARED_ROOT"
chmod 700 "$LOG_ROOT" "$SHARED_ROOT" 2>/dev/null || true

# A product command may verify the wrapper itself.  The current Executor owns
# the run in this case, so avoid contending for its lock or starting a nested
# CI process.
if [ "${LOCALCI_EXECUTOR_RUN:-0}" = "1" ]; then
  echo "CI delegated to current localCI executor"
  exit 0
fi

if command -v shasum >/dev/null 2>&1; then
  KEY="$(printf '%s' "$ROOT_DIR" | shasum -a 256 | cut -d ' ' -f 1)"
elif command -v sha256sum >/dev/null 2>&1; then
  KEY="$(printf '%s' "$ROOT_DIR" | sha256sum | cut -d ' ' -f 1)"
else
  KEY="$(ROOT_DIR="$ROOT_DIR" python3 -c 'import hashlib, os; print(hashlib.sha256(os.environ["ROOT_DIR"].encode()).hexdigest())')"
fi
LOCK_DIR="$SHARED_ROOT/$KEY.lock"
RESULT="$SHARED_ROOT/$KEY.result"
LOG="$LOG_ROOT/$KEY.log"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "ci_state: shared_in_flight"
  echo "ci_owner_lock: $LOCK_DIR"
  exit 75
fi
cleanup() { rmdir "$LOCK_DIR" 2>/dev/null || true; }
trap cleanup EXIT

started="$(date +%s)"
if bash "$ROOT_DIR/scripts/ci_local.sh" >"$LOG" 2>&1; then status=0; else status=$?; fi
elapsed=$(( $(date +%s) - started ))
printf 'status=%s\nelapsed_seconds=%s\nlog=%s\n' "$status" "$elapsed" "$LOG" >"$RESULT"
chmod 600 "$RESULT" "$LOG" 2>/dev/null || true

if [ "$status" -eq 0 ]; then
  echo "CI passed (policy + product; ${elapsed}s)"
else
  echo "CI failed (exit ${status}; see ${LOG})" >&2
  if ! python3 "$ROOT_DIR/scripts/ci_log_excerpt.py" "$LOG" >&2; then
    echo "CI error excerpt unavailable; inspect the protected full log locally." >&2
  fi
fi
exit "$status"
