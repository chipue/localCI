#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if ! grep -Eq '^  enabled: true$' "$ROOT_DIR/.agent-ci-policy.yml"; then
  echo "GitHub Actions is disabled. Set github.enabled: true after creating the repository." >&2
  exit 2
fi
echo "GitHub pre-merge dispatch is not configured yet; add repository-specific PR checks first." >&2
exit 2
