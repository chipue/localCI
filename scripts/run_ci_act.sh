#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
exec "$ROOT_DIR/localci" run \
  --profile "${AGENT_CI_PROFILE:-standard}" \
  --backend act \
  --inventory "${AGENT_CI_MANIFEST:-.localci/product-commands.json}"
