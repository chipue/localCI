#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
git config core.hooksPath .localci/hooks
chmod +x .localci/hooks/pre-commit .localci/hooks/pre-push
echo "localCI hooks installed at .localci/hooks"
