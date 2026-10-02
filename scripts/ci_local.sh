#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
python3 scripts/policy_verify.py

# Product test commands can invoke the quiet wrapper as part of their own
# evaluation.  Do not recursively start another product run from Executor.
if [ "${LOCALCI_EXECUTOR_RUN:-0}" = "1" ]; then
  echo "product checks: delegated to current localCI executor"
  exit 0
fi

MANIFEST="${AGENT_CI_MANIFEST:-$ROOT_DIR/.localci/product-commands.json}"
if [ ! -f "$MANIFEST" ]; then
  echo "product checks: not configured"
  exit 2
fi

python3 scripts/validate_command_manifest.py "$MANIFEST"
BACKEND="${AGENT_CI_BACKEND:-auto}"
DIFF_FLAG=""
if [ "${AGENT_CI_DIFF:-0}" = "1" ]; then
  DIFF_FLAG="--diff"
fi
RESULT_ARGS=()
if [ -n "${AGENT_CI_RESULT_FILE:-}" ]; then
  RESULT_ARGS+=(--result-file "$AGENT_CI_RESULT_FILE")
fi
if [ -n "${AGENT_CI_BASE:-}" ]; then
  python3 scripts/localci_run.py \
    --profile "${AGENT_CI_PROFILE:-standard}" \
    --backend "$BACKEND" \
    --inventory "$MANIFEST" \
    --base "$AGENT_CI_BASE" ${DIFF_FLAG} "${RESULT_ARGS[@]+${RESULT_ARGS[@]}}"
else
  python3 scripts/localci_run.py \
    --profile "${AGENT_CI_PROFILE:-standard}" \
    --backend "$BACKEND" \
    --inventory "$MANIFEST" ${DIFF_FLAG} "${RESULT_ARGS[@]+${RESULT_ARGS[@]}}"
fi
