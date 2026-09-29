#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
python3 scripts/policy_verify.py

if grep -Eq '^  full_ci: [^n].*' .agent-ci-policy.yml; then
  echo "product full_ci is configured; connect it here after reviewing the command"
else
  echo "product checks: not configured"
fi
